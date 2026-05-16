import discord
from discord import app_commands, Interaction
from discord.ext import commands
import psycopg2
from psycopg2.extras import RealDictCursor
import random
import os
import threading
import asyncio
from flask import Flask
import datetime 
import string
import time

# ================= CONFIGURAZIONE =================
# Puoi cambiare queste variabili d'ambiente su Render per il secondo bot
TOKEN = os.environ.get("TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")
RUOLO_STAFF_ID = 1253460150141059198

intents = discord.Intents.default()
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

# Lista dei server autorizzati per il secondo bot (Modifica gli ID se necessario)
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
            # Se l'utente non esiste, lo registra con 0 contanti e 0 banca
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
    
    # Sincronizzazione Comandi Slash
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

# ================= COMANDI ECONOMIA =================
import asyncio
import discord
from discord.ext import commands, tasks
from datetime import datetime, timedelta

import asyncio
import discord
from discord import app_commands
from discord.ext import commands, tasks
from datetime import datetime, timedelta

# ID Utente Amministratore Unico richiesto
ADMIN_ID_CONFIG = 1253460178305679433

class SistemaRP(commands.GroupCog, name="rp"):
    def __init__(self, bot: commands.Bot, db_connection):
        self.bot = bot
        self.db = db_connection  # Connessione psycopg2 attiva
        self.controllo_finanziamenti.start()

    def cog_unload(self):
        self.controllo_finanziamenti.cancel()

    # ==========================================
    # TASK AUTOMATICO GIORNALIERO (FINANZIAMENTI)
    # ==========================================
    @tasks.loop(hours=24.0)
    async def controllo_finanziamenti(self):
        """Controlla ogni 24 ore i finanziamenti attivi e scala la quota dalla banca."""
        await asyncio.to_thread(self._elabora_finanziamenti_giornalieri)

    def _elabora_finanziamenti_giornalieri(self):
        """Logica sincrona per psycopg2 eseguita in background (tabella public.finanziamenti e public.users)."""
        with self.db.cursor() as cursor:
            # Estrae i finanziamenti attivi
            cursor.execute("SELECT id, user_id, importo_giornaliero, giorni_rimanenti FROM public.finanziamenti WHERE giorni_rimanenti > 0;")
            finanziamenti = cursor.fetchall()
            
            for f in finanziamenti:
                f_id, user_id, quota, giorni_rimanenti = f
                
                # Controlla il saldo 'bank' dell'utente nella tabella public.users
                cursor.execute("SELECT bank FROM public.users WHERE user_id = %s;", (user_id,))
                res = cursor.fetchone()
                saldo_banca = res[0] if res else None
                
                if saldo_banca is not None and saldo_banca >= quota:
                    # Sottrae la quota dalla banca dell'utente (Tabella public.users)
                    cursor.execute("UPDATE public.users SET bank = bank - %s WHERE user_id = %s;", (quota, user_id))
                    
                    nuovi_giorni = giorni_rimanenti - 1
                    if nuovi_giorni <= 0:
                        cursor.execute("DELETE FROM public.finanziamenti WHERE id = %s;", (f_id,))
                    else:
                        cursor.execute("UPDATE public.finanziamenti SET giorni_rimanenti = %s WHERE id = %s;", (nuovi_giorni, f_id))
                else:
                    print(f"[FINANZIAMENTI] L'utente {user_id} non ha abbastanza fondi ({saldo_banca}€) per coprire la quota di {quota}€.")
            
            self.db.commit()

    @controllo_finanziamenti.before_loop
    async def before_controllo_finanziamenti(self):
        await self.bot.wait_until_ready()

    # ==========================================
    # SLASH COMMAND: MOSTRA PATENTE
    # ==========================================
    @app_commands.command(name="mostra_patente", description="Mostra la tua patente o quella di un altro cittadino")
    @app_commands.describe(utente="L'utente di cui vuoi vedere la patente")
    async def mostra_patente(self, interaction: discord.Interaction, utente: discord.Member = None):
        utente = utente or interaction.user
        uid = str(utente.id)

        def get_patente_data():
            with self.db.cursor() as cursor:
                # Tabella public.documenti
                cursor.execute("SELECT nome, cognome, data_nascita, foto_url FROM public.documenti WHERE user_id = %s;", (uid,))
                doc = cursor.fetchone()
                
                # Tabella public.patenti_registrate
                cursor.execute("SELECT tipo FROM public.patenti_registrate WHERE user_id = %s;", (uid,))
                patenti = cursor.fetchall()
                return doc, patenti

        doc, patenti = await asyncio.to_thread(get_patente_data)

        if not doc:
            return await interaction.response.send_message(f"❌ Nessun documento d'identità trovato nel database per {utente.mention}.", ephemeral=True)

        elenco_patenti = ", ".join([p[0] for p in patenti]) if patenti else "Nessuna patente registrata"

        embed = discord.Embed(
            title=f"💳 Patente di Guida: {doc[0]} {doc[1]}",
            color=discord.Color.blue()
        )
        embed.add_field(name="👤 Titolare", value=f"{doc[0]} {doc[1]}", inline=True)
        embed.add_field(name="📅 Data di Nascita", value=doc[2] or "N/D", inline=True)
        embed.add_field(name="🚗 Categorie Abilitate", value=f"**{elenco_patenti}**", inline=False)
        
        if doc[3]: # foto_url
            embed.set_thumbnail(url=doc[3])

        await interaction.response.send_message(embed=embed)

    # ==========================================
    # SLASH COMMAND: STATO VEICOLO (PUBBLICO)
    # ==========================================
    @app_commands.command(name="stato_veicolo", description="Controlla lo stato legale, assicurativo e meccanico di un veicolo")
    @app_commands.describe(targa="La targa del veicolo da controllare")
    async def stato_veicolo(self, interaction: discord.Interaction, targa: str):
        targa_pulita = targa.upper()
        
        def get_veicolo_data():
            with self.db.cursor() as cursor:
                # Tabella public.veicoli con campi integrati
                cursor.execute(
                    """SELECT targa, modello, owner_id, sequestrato, 
                              assicurato, data_scadenza_assicurazione, 
                              revisionato, data_scadenza_revisione 
                       FROM public.veicoli WHERE targa = %s;""", (targa_pulita,)
                )
                return cursor.fetchone()

        veicolo = await asyncio.to_thread(get_veicolo_data)

        if not veicolo:
            return await interaction.response.send_message("❌ Veicolo non trovato nel database con la targa inserita.", ephemeral=True)

        v_targa, v_modello, v_owner, v_sequestrato, v_assicurato, v_scad_ass, v_revisionato, v_scad_rev = veicolo
        proprietario = f"<@{v_owner}>" if v_owner else "Sconosciuto"
        
        embed = discord.Embed(title=f"🚘 Verifica Stato Veicolo: {v_targa}", color=discord.Color.dark_green())
        embed.add_field(name="Modello", value=v_modello or "N/D", inline=True)
        embed.add_field(name="Proprietario", value=proprietario, inline=True)
        embed.add_field(name="Stato Sequestro", value="🔴 SEQUESTRATO" if v_sequestrato else "🟢 LIBERO", inline=True)
        
        info_ass = f"🟢 Attiva\n📅 Scadenza: {v_scad_ass}" if v_assicurato else "🔴 NON ASSICURATO"
        embed.add_field(name="🛡️ Assicurazione", value=info_ass, inline=False)
        
        info_rev = f"🟢 Valida\n📅 Scadenza: {v_scad_rev}" if v_revisionato else "🔴 SCADUTA / NON REVISIONATO"
        embed.add_field(name="🔧 Revisione Statale", value=info_rev, inline=False)

        await interaction.response.send_message(embed=embed)

    # ==========================================
    # SLASH COMMANDS AMMINISTRATIVI BLINDATI
    # ==========================================
    
    @app_commands.command(name="crea_finanziamento", description="[ADMIN ONLY] Attiva un piano di ammortamento giornaliero su un utente")
    @app_commands.describe(
        utente="L'utente a cui intestare il finanziamento",
        prezzo_totale="L'ammontare totale del finanziamento",
        durata_giorni="In quanti giorni (prelievi) deve essere estinto",
        motivo="La causale del finanziamento"
    )
    async def crea_finanziamento(self, interaction: discord.Interaction, utente: discord.Member, prezzo_totale: float, durata_giorni: int, motivo: str = "Finanziamento Veicolo"):
        # Controllo rigido dell'ID Utente richiesto
        if interaction.user.id != ADMIN_ID_CONFIG:
            return await interaction.response.send_message("⛔ Questo comando è riservato esclusivamente all'amministratore del sistema.", ephemeral=True)

        if durata_giorni <= 0:
            return await interaction.response.send_message("❌ La durata del finanziamento deve essere di almeno 1 giorno.", ephemeral=True)

        quota_giornaliera = round(prezzo_totale / durata_giorni, 2)

        def insert_finanziamento():
            with self.db.cursor() as cursor:
                cursor.execute(
                    """INSERT INTO public.finanziamenti (user_id, importo_giornaliero, giorni_rimanenti, descrizione) 
                       VALUES (%s, %s, %s, %s);""",
                    (str(utente.id), quota_giornaliera, durata_giorni, motivo)
                )
            self.db.commit()

        await asyncio.to_thread(insert_finanziamento)

        await interaction.response.send_message(
            f"✅ **Finanziamento Registrato**\n"
            f"👤 **Beneficiario:** {utente.mention}\n"
            f"💰 **Importo Totale:** {prezzo_totale}€\n"
            f"📅 **Durata:** {durata_giorni} giorni\n"
            f"📉 **Prelievo Giornaliero:** {quota_giornaliera}€/giorno (automatico da `public.users.bank`)"
        )

    @app_commands.command(name="gestisci_veicolo", description="[ADMIN ONLY] Aggiorna Assicurazione o Revisione di un veicolo")
    @app_commands.describe(
        azione="Scegli se aggiornare l'assicurazione o la revisione",
        targa="La targa del veicolo",
        giorni="I giorni di validità del rinnovo da oggi"
    )
    @app_commands.choices(azione=[
        app_commands.Choice(name="Assicurazione", value="assicurazione"),
        app_commands.Choice(name="Revisione", value="revisione")
    ])
    async def gestisci_veicolo(self, interaction: discord.Interaction, azione: app_commands.Choice[str], targa: str, giorni: int = 30):
        # Controllo rigido dell'ID Utente richiesto
        if interaction.user.id != ADMIN_ID_CONFIG:
            return await interaction.response.send_message("⛔ Questo comando è riservato esclusivamente all'amministratore del sistema.", ephemeral=True)

        targa_pulita = targa.upper()
        data_scadenza = (datetime.now() + timedelta(days=giorni)).strftime("%d/%m/%Y")

        def update_veicolo():
            with self.db.cursor() as cursor:
                # Tabella public.veicoli
                cursor.execute("SELECT 1 FROM public.veicoli WHERE targa = %s;", (targa_pulita,))
                if not cursor.fetchone():
                    return "not_found"

                if i_azione := azione.value == "assicurazione":
                    cursor.execute("UPDATE public.veicoli SET assicurato = true, data_scadenza_assicurazione = %s WHERE targa = %s;", (data_scadenza, targa_pulita))
                elif azione.value == "revisione":
                    cursor.execute("UPDATE public.veicoli SET revisionato = true, data_scadenza_revisione = %s WHERE targa = %s;", (data_scadenza, targa_pulita))
                
            self.db.commit()
            return "success"

        risultato = await asyncio.to_thread(update_veicolo)

        if resultado == "not_found":
            await interaction.response.send_message(f"❌ Nessun veicolo associato alla targa `{targa_pulita}` all'interno della tabella `public.veicoli`.", ephemeral=True)
        else:
            await interaction.response.send_message(f"✅ **{azione.name} Aggiornata**: Veicolo targa **{targa_pulita}** messo in regola fino al `{data_scadenza}`.")

async def setup(bot: commands.Bot):
    # Esempio di caricamento nel tuo file principale:
    # await bot.add_cog(SistemaRP(bot, la_tua_connessione_psycopg2))
    pass

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

    # Assicuriamoci che il destinatario esista nel database prima di inviare i soldi
    get_user_data(utente.id)

    conn = get_db_connection()
    if not conn:
        return await interaction.followup.send("❌ Errore tecnico di connessione al database.", ephemeral=True)

    try:
        cur = conn.cursor()

        # Eseguiamo la sottrazione. Se i fondi non bastano e c'è il vincolo CHECK, 
        # PostgreSQL bloccherà l'operazione saltando direttamente al blocco 'except'
        cur.execute("UPDATE users SET bank = bank - %s WHERE id = %s", (ammontare, sender_id))
        
        # Eseguiamo l'accredito al destinatario
        cur.execute("UPDATE users SET bank = bank + %s WHERE id = %s", (ammontare, receiver_id))

        # Conferma definitiva dell'operazione
        conn.commit()

        embed = discord.Embed(
            title="🏦 BONIFICO BANCARIO ESEGUITO",
            color=discord.Color.blue(),
            timestamp=datetime.datetime.now()
        )
        embed.add_field(name="👤 Mittente", value=interaction.user.mention, inline=True)
        embed.add_field(name="👤 Destinatario", value=utente.mention, inline=True)
        embed.add_field(name="💰 Somma Inviata", value=f"**{ammontare:,}$**", inline=False)
        embed.set_footer(text="Transazione bancaria tracciata ed eseguita")
        
        await interaction.followup.send(embed=embed)

    except psycopg2.errors.CheckViolation:
        # Questo blocco scatta se l'utente viola il CHECK constraint (va sotto zero in banca)
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
bot.run(TOKEN)
