import discord
from discord import app_commands, Interaction
from discord.ext import commands, tasks
import psycopg2
from psycopg2.extras import RealDictCursor
import random
import os
import threading
import asyncio
from flask import Flask
import datetime 
from datetime import datetime, timedelta
import string
import time

# ================= CONFIGURAZIONE =================
TOKEN = os.environ.get("TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")

# ID dei ruoli separati per i rispettivi comandi
RUOLO_ASSICURAZIONE_ID = 1253460150141059198  
RUOLO_REVISIONE_ID = 1253460178305679433      

intents = discord.Intents.default()
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

# Lista dei server autorizzati per il secondo bot
ALLOWED_GUILDS = [1383905374092005376, 1233353915559313478, 1392825183915610205]

# ================= DATABASE SETUP =================

def get_db_connection():
    try:
        url = DATABASE_URL.replace("postgres://", "postgresql://")
        conn = psycopg2.connect(url, sslmode='require', connect_timeout=10)
        return conn
    except Exception as e:
        print(f"❌ Errore connessione DB: {e}")
        return None

# Funzione helper per recuperare i dati utente
def get_user_data(user_id):
    conn = get_db_connection()
    if not conn:
        return {"wallet": 0, "bank": 0}
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT wallet, bank FROM users WHERE id = %s", (str(user_id),))
        res = cur.fetchone()
        if not res:
            cur.execute("INSERT INTO users (id, wallet, bank) VALUES (%s, 0, 0)", (str(user_id),))
            conn.commit()
            return {"wallet": 0, "bank": 0}
        return res
    except Exception as e:
        print(f"Errore recupero dati utente: {e}")
        return {"wallet": 0, "bank": 0}
    finally:
        if conn:
            cur.close()
            conn.close()

# ================= EVENTI INIZIALI =================

@bot.event
async def on_ready():
    print(f'{"="*40}')
    print(f'🤖 LOG IN SECONDO BOT: {bot.user}')
    
    # Avvia il loop dei finanziamenti all'avvio del bot
    if not controllo_finanziamenti.is_running():
        controllo_finanziamenti.start()
    
    try:
        print("🔄 Sincronizzazione comandi slash...")
        synced = await bot.tree.sync()
        print(f"🔄 Sincronizzati {len(synced)} comandi!")
    except Exception as e:
        print(f"❌ Errore durante la sincronizzazione: {e}")

    print(f"✅ Bot Online e pronto all'uso!")
    print(f'{"="*40}')

# --- Controllo globale autorizzazione server ---
@bot.tree.interaction_check
async def check_guild(interaction: discord.Interaction):
    if interaction.guild_id not in ALLOWED_GUILDS:
        await interaction.response.send_message("❌ Questo bot non è autorizzato in questo server.", ephemeral=True)
        return False
    return True

# ==========================================
# TASK AUTOMATICO GIORNALIERO (FINANZIAMENTI)
# ==========================================
@tasks.loop(hours=24.0)
async def controllo_finanziamenti():
    """Controlla ogni 24 ore i finanziamenti attivi e scala la quota dalla banca."""
    await asyncio.to_thread(_elabora_finanziamenti_giornalieri)

def _elabora_finanziamenti_giornalieri():
    db = get_db_connection()
    if not db:
        return
    try:
        with db.cursor() as cursor:
            cursor.execute("SELECT id, user_id, importo_giornaliero, giorni_rimanenti FROM public.finanziamenti WHERE giorni_rimanenti > 0;")
            finanziamenti = cursor.fetchall()
            
            for f in finanziamenti:
                f_id, user_id, quota, giorni_rimanenti = f
                
                cursor.execute("SELECT bank FROM public.users WHERE user_id = %s;", (user_id,))
                res = cursor.fetchone()
                saldo_banca = res[0] if res else None
                
                if saldo_banca is not None and saldo_banca >= quota:
                    cursor.execute("UPDATE public.users SET bank = bank - %s WHERE user_id = %s;", (quota, user_id))
                    
                    nuovi_giorni = giorni_rimanenti - 1
                    if nuovi_giorni <= 0:
                        cursor.execute("DELETE FROM public.finanziamenti WHERE id = %s;", (f_id,))
                    else:
                        cursor.execute("UPDATE public.finanziamenti SET giorni_rimanenti = %s WHERE id = %s;", (nuovi_giorni, f_id))
                else:
                    print(f"[FINANZIAMENTI] L'utente {user_id} non ha abbastanza fondi ({saldo_banca}€) per coprire la quota di {quota}€.")
            db.commit()
    except Exception as e:
        print(f"Errore nel task finanziamenti: {e}")
    finally:
        db.close()

@controllo_finanziamenti.before_loop
async def before_controllo_finanziamenti():
    await bot.wait_until_ready()

# ==========================================
# BOT TREE: MOSTRA PATENTE (PERSONALE)
# ==========================================
@bot.tree.command(name="mostra_patente", description="Mostra il tuo documento e le tue patenti registrate")
async def mostra_patente(interaction: discord.Interaction):
    uid = str(interaction.user.id)
    conn = get_db_connection()
    if not conn:
        return await interaction.response.send_message("❌ Errore tecnico di connessione al database.", ephemeral=True)

    def get_patente_data():
        with conn.cursor() as cursor:
            cursor.execute("SELECT nome, cognome, data_nascita, foto_url FROM public.documenti WHERE user_id = %s;", (uid,))
            doc = cursor.fetchone()
            
            cursor.execute("SELECT tipo FROM public.patenti_registrate WHERE user_id = %s;", (uid,))
            patenti = cursor.fetchall()
            return doc, patenti

    doc, patenti = await asyncio.to_thread(get_patente_data)
    conn.close()

    if not doc:
        return await interaction.response.send_message("❌ Nessun documento d'identità personale trovato nel database. Registrati prima all'anagrafe.", ephemeral=True)

    elenco_patenti = ", ".join([p[0] for p in patenti]) if patenti else "Nessuna patente registrata"

    embed = discord.Embed(
        title=f"💳 Patente di Guida: {doc[0]} {doc[1]}",
        color=discord.Color.blue()
    )
    embed.add_field(name="👤 Titolare", value=f"{doc[0]} {doc[1]}", inline=True)
    embed.add_field(name="📅 Data di Nascita", value=doc[2] or "N/D", inline=True)
    embed.add_field(name="🚗 Categorie Abilitate", value=f"**{elenco_patenti}**", inline=False)
    
    if doc[3]: 
        embed.set_thumbnail(url=doc[3])

    await interaction.response.send_message(embed=embed)

# ==========================================
# BOT TREE: STATO FINANZIAMENTO (PERSONALE)
# ==========================================
@bot.tree.command(name="stato_finanziamento", description="Controlla la situazione dei tuoi finanziamenti attivi")
async def stato_finanziamento(interaction: discord.Interaction):
    uid = str(interaction.user.id)
    conn = get_db_connection()
    if not conn:
        return await interaction.response.send_message("❌ Errore tecnico di connessione al database.", ephemeral=True)

    def get_finanziamenti():
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT importo_giornaliero, giorni_rimanenti, descrizione FROM public.finanziamenti WHERE user_id = %s AND giorni_rimanenti > 0;", 
                (uid,)
            )
            return cursor.fetchall()

    finanziamenti = await asyncio.to_thread(get_finanziamenti)
    conn.close()

    if not finanziamenti:
        return await interaction.response.send_message("ℹ️ Non hai nessun finanziamento o piano di ammortamento attivo a tuo nome.", ephemeral=True)

    embed = discord.Embed(
        title="🏦 I tuoi Finanziamenti Attivi",
        color=discord.Color.gold(),
        timestamp=datetime.now()
    )

    for idx, f in enumerate(finanziamenti, 1):
        quota, giorni, desc = f
        totale_residuo = round(quota * giorni, 2)
        embed.add_field(
            name=f"📋 Finanziamento #{idx}: {desc}",
            value=f"➡️ **Quota Giornaliera:** {quota}€\n"
                  f"📅 **Giorni Rimanenti:** {giorni}\n"
                  f"💰 **Debito Residuo:** {totale_residuo}€",
            inline=False
        )

    embed.set_footer(text=f"Richiesto da: {interaction.user.display_name}")
    await interaction.response.send_message(embed=embed)

# ==========================================
# BOT TREE: CREA FINANZIAMENTO (LIBERO)
# ==========================================
@bot.tree.command(name="crea_finanziamento", description="Attiva un piano di ammortamento giornaliero su un utente")
@app_commands.describe(
    utente="L'utente a cui intestare il finanziamento",
    prezzo_totale="L'ammontare totale del finanziamento",
    durata_giorni="In quanti giorni (prelievi) deve essere estinto",
    motivo="La causale del finanziamento"
)
async def crea_finanziamento(interaction: discord.Interaction, utente: discord.Member, prezzo_totale: float, durata_giorni: int, motivo: str = "Finanziamento Veicolo"):
    if durata_giorni <= 0:
        return await interaction.response.send_message("❌ La durata del finanziamento deve essere di almeno 1 giorno.", ephemeral=True)

    quota_giornaliera = round(prezzo_totale / durata_giorni, 2)
    conn = get_db_connection()
    if not conn:
        return await interaction.response.send_message("❌ Errore tecnico di connessione al database.", ephemeral=True)

    def insert_finanziamento():
        with conn.cursor() as cursor:
            cursor.execute(
                """INSERT INTO public.finanziamenti (user_id, importo_giornaliero, giorni_rimanenti, descrizione) 
                   VALUES (%s, %s, %s, %s);""",
                (str(utente.id), quota_giornaliera, durata_giorni, motivo)
            )
        conn.commit()

    await asyncio.to_thread(insert_finanziamento)
    conn.close()

    await interaction.response.send_message(
        f"✅ **Finanziamento Registrato**\n"
        f"👤 **Beneficiario:** {utente.mention}\n"
        f"💰 **Importo Totale:** {prezzo_totale}€\n"
        f"📅 **Durata:** {durata_giorni} giorni\n"
        f"📉 **Prelievo Giornaliero:** {quota_giornaliera}€/giorno (automatico da `public.users.bank`)"
    )

# ==========================================
# BOT TREE: COMANDO ASSICURAZIONE (RUOLO SPECIFICO)
# ==========================================
@bot.tree.command(name="assicurazione", description="Rinnova o imposta l'assicurazione attiva di un veicolo tramite targa")
@app_commands.describe(targa="La targa del veicolo da assicurare", giorni="Giorni di validità dell'assicurazione")
async def assicurazione(interaction: discord.Interaction, targa: str, giorni: int = 30):
    user_roles_ids = [role.id for role in interaction.user.roles]
    if RUOLO_ASSICURAZIONE_ID not in user_roles_ids:
        return await interaction.response.send_message("⛔ Non hai il ruolo staff autorizzato per gestire l'Assicurazione.", ephemeral=True)

    targa_pulita = targa.upper()
    data_scadenza = (datetime.now() + timedelta(days=giorni)).strftime("%d/%m/%Y")
    conn = get_db_connection()
    if not conn:
        return await interaction.response.send_message("❌ Errore tecnico di connessione al database.", ephemeral=True)

    def update_assicurazione():
        with conn.cursor() as cursor:
            cursor.execute("SELECT 1 FROM public.veicoli WHERE targa = %s;", (targa_pulita,))
            if not cursor.fetchone():
                return "not_found"
            cursor.execute("UPDATE public.veicoli SET assicurato = true, data_scadenza_assicurazione = %s WHERE targa = %s;", (data_scadenza, targa_pulita))
        conn.commit()
        return "success"

    risultato = await asyncio.to_thread(update_assicurazione)
    conn.close()

    if risultato == "not_found":
        await interaction.response.send_message(f"❌ Nessun veicolo associato alla targa `{targa_pulita}` nella tabella `public.veicoli`.", ephemeral=True)
    else:
        await interaction.response.send_message(f"✅ **Assicurazione Aggiornata**: Il veicolo con targa **{targa_pulita}** è ora assicurato fino al `{data_scadenza}`.")

# ==========================================
# BOT TREE: COMANDO REVISIONE (RUOLO SPECIFICO)
# ==========================================
@bot.tree.command(name="revisione", description="Rinnova o imposta la revisione statale di un veicolo tramite targa")
@app_commands.describe(targa="La targa del veicolo da revisionare", giorni="Giorni di validità della revisione")
async def revisione(interaction: discord.Interaction, targa: str, giorni: int = 30):
    user_roles_ids = [role.id for role in interaction.user.roles]
    if RUOLO_REVISIONE_ID not in user_roles_ids:
        return await interaction.response.send_message("⛔ Non hai il ruolo staff autorizzato per gestire la Revisione.", ephemeral=True)

    targa_pulita = targa.upper()
    data_scadenza = (datetime.now() + timedelta(days=giorni)).strftime("%d/%m/%Y")
    conn = get_db_connection()
    if not conn:
        return await interaction.response.send_message("❌ Errore tecnico di connessione al database.", ephemeral=True)

    def update_revisione():
        with conn.cursor() as cursor:
            cursor.execute("SELECT 1 FROM public.veicoli WHERE targa = %s;", (targa_pulita,))
            if not cursor.fetchone():
                return "not_found"
            cursor.execute("UPDATE public.veicoli SET revisionato = true, data_scadenza_revisione = %s WHERE targa = %s;", (data_scadenza, targa_pulita))
        conn.commit()
        return "success"

    risultato = await asyncio.to_thread(update_revisione)
    conn.close()

    if risultato == "not_found":
        await interaction.response.send_message(f"❌ Nessun veicolo associato alla targa `{targa_pulita}` nella tabella `public.veicoli`.", ephemeral=True)
    else:
        await interaction.response.send_message(f"✅ **Revisione Aggiornata**: Il veicolo con targa **{targa_pulita}** è stato revisionato con successo fino al `{data_scadenza}`.")
# ==========================================
# BOT TREE: BONIFICO
# ==========================================
@bot.tree.command(name="bonifico", description="Invia denaro dalla tua banca alla banca di un altro utente")
@app_commands.describe(utente="L'utente che riceverà il denaro", ammontare="La quantità di denaro da inviare")
async def bonifico(interaction: discord.Interaction, utente: discord.Member, ammontare: int):
    # Evita il timeout dei 3 secondi di Discord
    await interaction.response.defer(ephemeral=False)

    if utente.id == interaction.user.id:
        return await interaction.followup.send("❌ Non puoi fare un bonifico a te stesso!", ephemeral=True)

    if ammontare <= 0:
        return await interaction.followup.send("❌ L'ammontare deve essere maggiore di zero!", ephemeral=True)

    sender_id = str(interaction.user.id)
    receiver_id = str(utente.id)

    # Assicuriamoci che il destinatario esista nella tabella users prima di inviare i soldi
    get_user_data(utente.id)

    conn = get_db_connection()
    if not conn:
        return await interaction.followup.send("❌ Errore tecnico di connessione al database.", ephemeral=True)

    try:
        cur = conn.cursor()

        # Modifica della colonna bank basata sulla tabella 'users' usando la chiave 'id'
        cur.execute("UPDATE users SET bank = bank - %s WHERE id = %s", (ammontare, sender_id))
        cur.execute("UPDATE users SET bank = bank + %s WHERE id = %s", (ammontare, receiver_id))

        # Conferma definitiva dell'operazione
        conn.commit()

        embed = discord.Embed(
            title="🏦 BONIFICO BANCARIO ESEGUITO",
            color=discord.Color.blue(),
            timestamp=datetime.now()
        )
        embed.add_field(name="👤 Mittente", value=interaction.user.mention, inline=True)
        embed.add_field(name="👤 Destinatario", value=utente.mention, inline=True)
        embed.add_field(name="💰 Somma Inviata", value=f"**{ammontare:,}$**", inline=False)
        embed.set_footer(text="Transazione bancaria tracciata ed eseguita")
        
        await interaction.followup.send(embed=embed)

    except psycopg2.errors.CheckViolation:
        # Questo blocco scatta se l'utente viola il CHECK constraint (se bank va sotto zero)
        conn.rollback()
        await interaction.followup.send("❌ Bonifico rifiutato: Non hai abbastanza denaro sul tuo conto bancario!", ephemeral=True)

    except Exception as e:
        conn.rollback()
        print(f"Errore imprevisto nel bonifico: {e}")
        await interaction.followup.send("❌ Si è verificato un errore tecnico durante l'operazione.", ephemeral=True)

    finally:
        if conn:
            cur.close()
            conn.close()


# ================= GESTIONE ERRORI GLOBALE =================

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    print(f"⚠️ Errore catturato: {error}")
    try:
        if not interaction.response.is_done():
            await interaction.response.send_message("❌ Si è verificato un errore imprevisto durante l'esecuzione del comando.", ephemeral=True)
        else:
            await interaction.followup.send("❌ Si è verificato un errore imprevisto durante l'esecuzione del comando.", ephemeral=True)
    except Exception as e:
        print(f"Impossibile notificare l'utente dell'errore: {e}")

# ================= CONFIGURAZIONE FLASK PER RENDER =================
app = Flask("")

@app.route("/")
def home(): 
    return "Bot Online"

def run(): 
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# Avvio del Web Server in un thread separato
threading.Thread(target=run, daemon=True).start()

# Avvio finale del Bot
if __name__ == "__main__":
    bot.run(TOKEN)
