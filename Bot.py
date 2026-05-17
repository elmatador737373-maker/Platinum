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
RUOLO_ASSICURAZIONE_ID = 1257780163656286281  
RUOLO_REVISIONE_ID = 125346018305350458      

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

from psycopg2.extras import RealDictCursor  # Assicurati di avere questo import in cima al file

async def invia_log_finanziario(guild: discord.Guild, embed: discord.Embed):
    """
    Recupera l'ID del canale dei log finanziari dal database e invia l'embed fornito.
    """
    try:
        conn = get_db_connection()
        if not conn:
            print("❌ [LOG FINANZE] Impossibile connettersi al database.")
            return

        # Utilizza RealDictCursor per mappare le colonne del DB come chiavi di un dizionario
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT setting_value FROM server_settings WHERE setting_name = 'log_finanze'")
        res = cur.fetchone()
        cur.close()
        conn.close()

        if res and res['setting_value']:
            # Converte l'ID memorizzato nel DB in un intero e cerca il canale nel server (guild)
            canale = guild.get_channel(int(res['setting_value']))
            if canale: 
                await canale.send(embed=embed)
            else:
                print(f"⚠️ [LOG FINANZE] Canale con ID {res['setting_value']} non trovato nel server.")
        else:
            print("⚠️ [LOG FINANZE] Impostazione 'log_finanze' non trovata nella tabella server_settings.")

    except Exception as e:
        print(f"❌ Errore durante l'invio del log finanziario: {e}")
# ==========================================
# TASK AUTOMATICO GIORNALIERO (FINANZIAMENTI)
# ==========================================
@tasks.loop(hours=24.0)
async def controllo_finanziamenti():
    """Controlla ogni 24 ore i finanziamenti attivi e scala la quota dalla banca."""
    # Eseguiamo i controlli sul DB e otteniamo la lista degli insolventi o dei log da inviare
    risultati = await asyncio.to_thread(_elabora_finanziamenti_giornalieri)
    
    if not risultati:
        return
        
    # Prendiamo la prima guild (server) disponibile in cui si trova il bot per cercare il canale log
    if not bot.guilds:
        return
    guild = bot.guilds[0] 

    # Gestione delle notifiche (Log e DM) per chi non ha pagato
    for insolvente in risultati["insolventi"]:
        uid = insolvente["user_id"]
        quota = insolvente["quota"]
        saldo = insolvente["saldo"]
        
        # 1. Invio Log nel canale del server (usando la tua funzione esistente)
        embed_log = discord.Embed(
            title="⚠️ Mancato Pagamento Finanziamento",
            description=f"L'utente <@{uid}> non ha abbastanza fondi in banca per coprire la quota giornaliera.",
            color=discord.Color.red(),
            timestamp=datetime.now()
        )
        embed_log.add_field(name="📉 Quota Giornaliera", value=f"{quota:,.2f}€", inline=True)
        embed_log.add_field(name="🏦 Saldo Attuale", value=f"{saldo:,.2f}€" if saldo is not None else "N/D", inline=True)
        embed_log.set_footer(text="Sistema Ammortamento Automatico")
        
        await invia_log_finanziario(guild, embed_log)
        
        # 2. Invio notifica in DM all'utente
        try:
            user = await bot.fetch_user(int(uid))
            if user:
                embed_user = discord.Embed(
                    title="🏦 Sollecito di Pagamento",
                    description=f"Ciao {user.display_name}, il tuo conto bancario non dispone di fondi sufficienti per saldare la quota odierna del finanziamento.",
                    color=discord.Color.orange(),
                    timestamp=datetime.now()
                )
                embed_user.add_field(name="📉 Quota da pagare", value=f"**{quota:,.2f}€**", inline=True)
                embed_user.add_field(name="💰 Il tuo Saldo", value=f"{saldo:,.2f}€" if saldo is not None else "0.00€", inline=True)
                embed_user.add_field(name="⚠️ Conseguenze", value="I giorni rimanenti del tuo piano sono stati congelati. Deposita il denaro al più presto per evitare sanzioni civili o pignoramenti.", inline=False)
                await user.send(embed=embed_user)
        except discord.Forbidden:
            print(f"[FINANZIAMENTI] Impossibile inviare DM a {uid} (Utente con DM chiusi).")
        except Exception as e:
            print(f"[FINANZIAMENTI] Errore invio DM a {uid}: {e}")


def _elabora_finanziamenti_giornalieri():
    db = get_db_connection()
    if not db:
        return None
        
    risultati_azione = {"insolventi": []}
    
    try:
        with db.cursor() as cursor:
            cursor.execute("SELECT id, user_id, importo_giornaliero, giorni_rimanenti FROM public.finanziamenti WHERE giorni_rimanenti > 0;")
            finanziamenti = cursor.fetchall()
            
            for f in finanziamenti:
                f_id, user_id, quota, giorni_rimanenti = f
                
                cursor.execute("SELECT bank FROM public.users WHERE user_id = %s;", (user_id,))
                res = cursor.fetchone()
                saldo_banca = res[0] if res else None
                
                # SE HA I SOLDI: Scala normalmente
                if saldo_banca is not None and saldo_banca >= quota:
                    cursor.execute("UPDATE public.users SET bank = bank - %s WHERE user_id = %s;", (quota, user_id))
                    
                    nuovi_giorni = giorni_rimanenti - 1
                    if nuovi_giorni <= 0:
                        cursor.execute("DELETE FROM public.finanziamenti WHERE id = %s;", (f_id,))
                    else:
                        cursor.execute("UPDATE public.finanziamenti SET giorni_rimanenti = %s WHERE id = %s;", (nuovi_giorni, f_id))
                
                # SE NON HA I SOLDI (O l'utente non esiste):
                else:
                    print(f"[FINANZIAMENTI] L'utente {user_id} insolvente. Fondi insufficienti ({saldo_banca}€) per la quota di {quota}€.")
                    
                    # OPZIONE AGGIUNTIVA (Facoltativa): Se vuoi applicare una multa automatica di ad esempio 50€ nel DB puoi decommentare qui:
                    # cursor.execute("UPDATE public.users SET bank = bank - 50 WHERE user_id = %s;", (user_id,))
                    
                    # Salviamo i dettagli per inviare i log asincroni fuori dal thread
                    risultati_azione["insolventi"].append({
                        "user_id": user_id,
                        "quota": quota,
                        "saldo": saldo_banca
                    })
                    
            db.commit()
    except Exception as e:
        print(f"Errore nel database task finanziamenti: {e}")
    finally:
        db.close()
        
    return risultati_azione

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

import discord
from discord import app_commands
import asyncio
from datetime import datetime

# ==========================================
# VIEW: PULSANTE DI FIRMA PER IL FINANZIAMENTO
# ==========================================
class FirmaFinanziamentoView(discord.ui.View):
    def __init__(self, beneficiario: discord.Member, staffer: discord.User | discord.Member, prezzo_totale: float, durata_giorni: int, quota_giornaliera: float, descrizione_completa: str, tipo_nome: str, motivo: str):
        super().__init__(timeout=300) # Il pulsante scade dopo 5 minuti
        self.beneficiario = beneficiario
        self.staffer = staffer
        self.prezzo_totale = prezzo_totale
        self.durata_giorni = durata_giorni
        self.quota_giornaliera = quota_giornaliera
        self.descrizione_completa = descrizione_completa
        self.tipo_nome = tipo_nome
        self.motivo = motivo

    @discord.ui.button(label="Firma Finanziamento", style=discord.ButtonStyle.success, emoji="✍️")
    async def firma_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Controllo di sicurezza: solo chi riceve il finanziamento può firmare
        if interaction.user.id != self.beneficiario.id:
            return await interaction.response.send_message(
                f"❌ Solo {self.beneficiario.mention} può firmare questo contratto.", 
                ephemeral=True
            )

        # Connessione al database ed inserimento (avviene SOLO alla firma)
        conn = get_db_connection()
        if not conn:
            return await interaction.response.send_message("❌ Errore tecnico di connessione al database.", ephemeral=True)

        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """INSERT INTO public.finanziamenti (user_id, importo_giornaliero, giorni_rimanenti, descrizione) 
                       VALUES (%s, %s, %s, %s);""",
                    (str(self.beneficiario.id), self.quota_giornaliera, self.durata_giorni, self.descrizione_completa)
                )
            conn.commit()
        except Exception as e:
            print(f"Errore DB: {e}")
            return await interaction.response.send_message("❌ Errore durante il salvataggio nei sistemi bancari.", ephemeral=True)
        finally:
            conn.close()

        # Disabilita il pulsante dopo la firma per evitare doppi clic
        self.clear_items()

        # Crea l'embed di conferma definitiva
        embed_confermato = discord.Embed(
            title="🏦 Finanziamento Attivato & Firmato",
            description=f"Il contratto è stato firmato digitalmente da {self.beneficiario.mention} ed è ora operativo.",
            color=discord.Color.green(), # Cambia in verde a conferma avvenuta
            timestamp=datetime.now()
        )
        embed_confermato.add_field(name="👤 Beneficiario", value=self.beneficiario.mention, inline=True)
        embed_confermato.add_field(name="📋 Tipologia", value=self.tipo_nome, inline=True)
        embed_confermato.add_field(name="📝 Causale/Motivo", value=self.motivo, inline=False)
        embed_confermato.add_field(name="💰 Importo Totale", value=f"{self.prezzo_totale:,.2f}€", inline=True)
        embed_confermato.add_field(name="📅 Durata Piano", value=f"{self.durata_giorni} giorni", inline=True)
        embed_confermato.add_field(name="📉 Scalo Giornaliero", value=f"**{self.quota_giornaliera}€** / giorno", inline=False)
        embed_confermato.set_footer(text=f"Approvato da: {self.staffer.display_name} | Firmato il")

        # Aggiorna il messaggio originale rimuovendo il pulsante e cambiando l'embed
        await interaction.response.edit_message(embed=embed_confermato, view=self)

    async def on_timeout(self):
        # Se nessuno firma entro 5 minuti, rimuove il pulsante per evitare bug
        self.clear_items()
        # Nota: Qui potresti anche modificare il messaggio dicendo "Contratto scaduto", 
        # ma serve il messaggio originale. Gestiamo la pulizia dei pulsanti in sicurezza.


# ==========================================
# BOT TREE: CREA FINANZIAMENTO (CON PULSANTE)
# ==========================================
@bot.tree.command(name="crea_finanziamento", description="Attiva un piano di ammortamento giornaliero su un utente (Richiede Firma)")
@app_commands.describe(
    utente="L'utente a cui intestare il finanziamento",
    tipo_finanziamento="La tipologia di finanziamento da attivare",
    prezzo_totale="L'ammontare totale del finanziamento",
    durata_giorni="In quanti giorni (prelievi) deve essere estinto",
    motivo="Dettagli aggiuntivi o causale specifica"
)
@app_commands.choices(tipo_finanziamento=[
    app_commands.Choice(name="🚗 Veicolo / Autovettura", value="Finanziamento Veicolo"),
    app_commands.Choice(name="🏠 Casa / Proprietà", value="Finanziamento Immobiliare"),
    app_commands.Choice(name="💼 Aziendale / Business", value="Finanziamento Aziendale"),
    app_commands.Choice(name="💰 Prestito Personale", value="Prestito Personale")
])
async def crea_finanziamento(
    interaction: discord.Interaction, 
    utente: discord.Member, 
    tipo_finanziamento: app_commands.Choice[str], 
    prezzo_totale: float, 
    durata_giorni: int, 
    motivo: str = "Nessun dettaglio aggiuntivo"
):
    if durata_giorni <= 0:
        return await interaction.response.send_message("❌ La durata del finanziamento deve essere di almeno 1 giorno.", ephemeral=True)
    if prezzo_totale <= 0:
        return await interaction.response.send_message("❌ L'importo totale deve essere maggiore di 0€.", ephemeral=True)

    quota_giornaliera = round(prezzo_totale / durata_giorni, 2)
    descrizione_completa = f"[{tipo_finanziamento.name}] {motivo}"

    # Creazione dell'Embed di PROPOSTA (In attesa di firma)
    embed_proposta = discord.Embed(
        title="📝 Proposta di Finanziamento in Attesa di Firma",
        description=f"{utente.mention}, è stato generato un contratto a tuo nome. Clicca sul pulsante sottostante per accettare e **firmare il piano di ammortamento**.",
        color=discord.Color.gold(),
        timestamp=datetime.now()
    )
    
    embed_proposta.add_field(name="👤 Beneficiario", value=utente.mention, inline=True)
    embed_proposta.add_field(name="📋 Tipologia", value=tipo_finanziamento.name, inline=True)
    embed_proposta.add_field(name="📝 Causale/Motivo", value=motivo, inline=False)
    
    embed_proposta.add_field(name="💰 Importo Totale", value=f"{prezzo_totale:,.2f}€", inline=True)
    embed_proposta.add_field(name="📅 Durata Piano", value=f"{durata_giorni} giorni", inline=True)
    embed_proposta.add_field(name="📉 Scalo Giornaliero", value=f"**{quota_giornaliera}€** / giorno", inline=False)
    
    embed_proposta.set_footer(text=f"Proposto da: {interaction.user.display_name} | In attesa di firma...")

    # Inizializza la View con il pulsante passando tutti i dati necessari per il DB
    view = FirmaFinanziamentoView(
        beneficiario=utente,
        staffer=interaction.user,
        prezzo_totale=prezzo_totale,
        durata_giorni=durata_giorni,
        quota_giornaliera=quota_giornaliera,
        descrizione_completa=descrizione_completa,
        tipo_nome=tipo_finanziamento.name,
        motivo=motivo
    )

    # Invia il messaggio pubblico con l'embed e il bottone
    await interaction.response.send_message(content=utente.mention, embed=embed_proposta, view=view)
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
        # Anche l'avviso di nessun finanziamento ora usa un Embed pulito ed elegante
        embed_vuoto = discord.Embed(
            title="🏦 I tuoi Finanziamenti",
            description="ℹ️ Non hai nessun finanziamento o piano di ammortamento attivo a tuo nome.",
            color=discord.Color.blue(),
            timestamp=datetime.now()
        )
        embed_vuoto.set_footer(text=f"Richiesto da: {interaction.user.display_name}")
        return await interaction.response.send_message(embed=embed_vuoto, ephemeral=True)

    # Inizializzazione dell'Embed principale (Stile dorato coerente con la proposta)
    embed = discord.Embed(
        title="🏦 Linee di Credito & Finanziamenti Attivi",
        description=f"Ecco il riepilogo della tua situazione finanziaria corrente, {interaction.user.mention}.",
        color=discord.Color.gold(),
        timestamp=datetime.now()
    )

    totale_debito_complessivo = 0.0

    for idx, f in enumerate(finanziamenti, 1):
        quota, giorni, desc = f
        totale_residuo = round(quota * giorni, 2)
        totale_debito_complessivo += totale_residuo

        # Separazione pulita dei dati in perfetto stile tabella/lista
        valore_field = (
            f"📉 **Quota Giornaliera:** {quota:,.2f}€ / giorno\n"
            f"📅 **Giorni Rimanenti:** {giorni} giorni\n"
            f"💰 **Debito Residuo:** **{totale_residuo:,.2f}€**\n"
            f"───────────────"
        )

        embed.add_field(
            name=f"📊 #{idx} | {desc}",
            value=valore_field,
            inline=False
        )

    # Campo finale riassuntivo se l'utente ha più di un finanziamento attivo
    if len(finanziamenti) > 1:
        embed.add_field(
            name="📊 Riepilogo Totale",
            value=f"💳 **Piani Attivi:** {len(finanziamenti)}\n"
                  f"🟥 **Debito Totale Accumulato:** **{totale_debito_complessivo:,.2f}€**",
            inline=False
        )

    embed.set_footer(text=f"Richiesto da: {interaction.user.display_name}")
    
    await interaction.response.send_message(embed=embed)

import discord
from discord import app_commands
from discord.ext import tasks
import asyncio
from datetime import datetime, timedelta

# ==========================================
# BACKGROUND TASK: CONTROLLO SCADENZE E AVVISI DM
# ==========================================
@tasks.loop(hours=24) # Gira una volta al giorno
async def controllo_scadenze_veicoli():
    conn = get_db_connection()
    if not conn:
        print("❌ [TASK SCADENZE] Errore di connessione al database.")
        return

    def elabora_scadenze_e_avvisi():
        oggi = datetime.now().date()
        domani = oggi + timedelta(days=1)
        
        scaduti_ass = []
        scaduti_rev = []
        avvisi_dm = [] # Lista di tuple: (owner_id, targa, tipo_scadenza)

        with conn.cursor() as cursor:
            # Recuperiamo i veicoli attivi. NOTA: Sostituisci 'owner_id' con il nome reale della tua colonna proprietario nel DB
            cursor.execute("SELECT targa, data_scadenza_assicurazione, data_scadenza_revisione, owner_id FROM public.veicoli WHERE assicurato = true OR revisionato = true;")
            veicoli = cursor.fetchall()
            
            for targa, scadenza_ass, scadenza_rev, owner_id in veicoli:
                # --- Controllo Assicurazione ---
                if scadenza_ass:
                    try:
                        data_ass = datetime.strptime(scadenza_ass, "%d/%m/%Y").date()
                        if oggi >= data_ass:
                            scaduti_ass.append(targa)
                        elif data_ass == domani and owner_id:
                            avvisi_dm.append((owner_id, targa, "l'Assicurazione"))
                    except ValueError:
                        pass
                
                # --- Controllo Revisione ---
                if scadenza_rev:
                    try:
                        data_rev = datetime.strptime(scadenza_rev, "%d/%m/%Y").date()
                        if oggi >= data_rev:
                            scaduti_rev.append(targa)
                        elif data_rev == domani and owner_id:
                            avvisi_dm.append((owner_id, targa, "la Revisione"))
                    except ValueError:
                        pass

            # Esegui gli update per i veicoli scaduti oggi
            if scaduti_ass:
                cursor.execute("UPDATE public.veicoli SET assicurato = false WHERE targa = ANY(%s);", (scaduti_ass,))
            if scaduti_rev:
                cursor.execute("UPDATE public.veicoli SET revisionato = false WHERE targa = ANY(%s);", (scaduti_rev,))
                
        conn.commit()
        conn.close()
        return avvisi_dm

    # Esegui i controlli sul database in un thread separato
    lista_avvisi = await asyncio.to_thread(elabora_scadenze_e_avvisi)

    # --- Invio dei messaggi in DM agli utenti per avvisarli 1 giorno prima ---
    for user_id, targa, tipo in lista_avvisi:
        try:
            user = await bot.fetch_user(int(user_id))
            if user:
                embed_dm = discord.Embed(
                    title="⚠️ Avviso Scadenza Veicolo",
                    description=f"Ciao {user.display_name}, ti avvisiamo che domani scadrà **{tipo}** del tuo veicolo.",
                    color=discord.Color.red(),
                    timestamp=datetime.now()
                )
                embed_dm.add_field(name="🚘 Veicolo", value=f"Targa: `{targa}`", inline=True)
                embed_dm.add_field(name="📌 Stato", value="Scadenza nelle prossime 24 ore", inline=True)
                embed_dm.set_footer(text="Notifiche automatiche Motorizzazione")
                
                await user.send(embed=embed_dm)
        except discord.Forbidden:
            print(f"⚠️ Impossibile inviare DM all'utente {user_id} (DMs chiusi).")
        except Exception as e:
            print(f"❌ Errore nell'invio del DM a {user_id}: {e}")

    print("🔄 [TASK SCADENZE] Controllo scadenze e invio avvisi DM completato.")


# Avvio del Task all'avvio del bot
@bot.event
async def on_ready():
    if not controllo_scadenze_veicoli.is_running():
        controllo_scadenze_veicoli.start()
    print(f"🤖 Bot pronto e connesso come {bot.user}")


# ==========================================
# BOT TREE: COMANDO ASSICURAZIONE (TESTUALE ORIGINALE - 7 GIORNI)
# ==========================================
@bot.tree.command(name="assicurazione", description="Rinnova o imposta l'assicurazione attiva di un veicolo tramite targa (Validità: 7 giorni)")
@app_commands.describe(targa="La targa del veicolo da assicurare")
async def assicurazione(interaction: discord.Interaction, targa: str):
    user_roles_ids = [role.id for role in interaction.user.roles]
    if RUOLO_ASSICURAZIONE_ID not in user_roles_ids:
        return await interaction.response.send_message("⛔ Non hai il ruolo staff autorizzato per gestire l'Assicurazione.", ephemeral=True)

    targa_pulita = targa.upper()
    # Impostato fisso a 7 giorni come richiesto
    data_scadenza = (datetime.now() + timedelta(days=7)).strftime("%d/%m/%Y")
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

    # Ripristinate le risposte e le tabelle di testo originali
    if risultato == "not_found":
        await interaction.response.send_message(f"❌ Nessun veicolo associato alla targa `{targa_pulita}` nella tabella `public.veicoli`.", ephemeral=True)
    else:
        await interaction.response.send_message(f"✅ **Assicurazione Aggiornata**: Il veicolo con targa **{targa_pulita}** è ora assicurato fino al `{data_scadenza}`.")


# ==========================================
# BOT TREE: COMANDO REVISIONE (TESTUALE ORIGINALE - 7 GIORNI)
# ==========================================
@bot.tree.command(name="revisione", description="Rinnova o imposta la revisione statale di un veicolo tramite targa (Validità: 7 giorni)")
@app_commands.describe(targa="La targa del veicolo da revisionare")
async def revisione(interaction: discord.Interaction, targa: str):
    user_roles_ids = [role.id for role in interaction.user.roles]
    if RUOLO_REVISIONE_ID not in user_roles_ids:
        return await interaction.response.send_message("⛔ Non hai il ruolo staff autorizzato per gestire la Revisione.", ephemeral=True)

    targa_pulita = targa.upper()
    # Impostato fisso a 7 giorni come richiesto
    data_scadenza = (datetime.now() + timedelta(days=7)).strftime("%d/%m/%Y")
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

    # Ripristinate le risposte e le tabelle di testo originali
    if risultato == "not_found":
        await interaction.response.send_message(f"❌ Nessun veicolo associato alla targa `{targa_pulita}` nella tabella `public.veicoli`.", ephemeral=True)
    else:
        await interaction.response.send_message(f"✅ **Revisione Aggiornata**: Il veicolo con targa **{targa_pulita}** è stato revisionato con successo fino al `{data_scadenza}`.")

# ==========================================
# BOT TREE: BONIFICO (ALLINEATO ALLA TABELLA)
# ==========================================
# ==========================================
# BOT TREE: COMANDO BONIFICO (SISTEMATO)
# ==========================================
@bot.tree.command(name="bonifico", description="Invia denaro dalla tua banca alla banca di un altro utente")
@app_commands.describe(utente="L'utente che riceverà il denaro", ammontare="La quantità di denaro da inviare")
async def bonifico(interaction: discord.Interaction, utente: discord.Member, ammontare: int):
    # Evita il timeout di 3 secondi di Discord deferendo la risposta
    await interaction.response.defer(ephemeral=False)

    if utente.id == interaction.user.id:
        return await interaction.followup.send("❌ Non puoi fare un bonifico a te stesso!", ephemeral=True)

    if ammontare <= 0:
        return await interaction.followup.send("❌ L'ammontare deve essere maggiore di zero!", ephemeral=True)

    sender_id = str(interaction.user.id)
    receiver_id = str(utente.id)

    # Inizializza il destinatario nel database se non è ancora registrato
    get_user_data(utente.id)

    conn = get_db_connection()
    if not conn:
        return await interaction.followup.send("❌ Errore tecnico di connessione al database.", ephemeral=True)

    # Variabile di controllo per sapere se la transazione è andata a buon fine
    successo = False

    try:
        cur = conn.cursor()
        
        # 1. Verifica manuale del saldo
        cur.execute("SELECT bank FROM public.users WHERE user_id = %s;", (sender_id,))
        mittente_data = cur.fetchone()
        
        if not mittente_data or mittente_data[0] < ammontare:
            cur.close()
            conn.close()
            return await interaction.followup.send("❌ Bonifico rifiutato: Non hai abbastanza denaro sul tuo conto bancario!", ephemeral=True)

        # 2. Esecuzione dei prelievi e depositi
        cur.execute("UPDATE public.users SET bank = bank - %s WHERE user_id = %s", (ammontare, sender_id))
        cur.execute("UPDATE public.users SET bank = bank + %s WHERE user_id = %s", (ammontare, receiver_id))
        
        # Salva le modifiche nel database
        conn.commit()
        successo = True

        # Genera embed di successo per l'utente
        embed = discord.Embed(
            title="🏦 BONIFICO BANCARIO ESEGUITO",
            color=discord.Color.blue(),
            timestamp=datetime.now()
        )
        embed.add_field(name="👤 Mittente", value=interaction.user.mention, inline=True)
        embed.add_field(name="👤 Destinatario", value=utente.mention, inline=True)
        embed.add_field(name="💰 Somma Inviata", value=f"**{ammontare:,}$**", inline=False)
        embed.set_footer(text="Transazione eseguita con successo")
        
        await interaction.followup.send(embed=embed)

    except Exception as e:
        if 'conn' in locals() and conn:
            conn.rollback()
        print(f"[ERROR] Errore imprevisto nel bonifico: {e}")
        await interaction.followup.send("❌ Si è verificato un errore tecnico durante l'operazione.", ephemeral=True)

    finally:
        # Chiudiamo le connessioni in modo sicuro
        if 'cur' in locals() and cur:
            cur.close()
        if 'conn' in locals() and conn:
            conn.close()

    # 3. LOGS FINANZIARI (Eseguiti fuori dal blocco Try/Finally del DB per evitare conflitti)
    if successo:
        try:
            emb = discord.Embed(title="💵 LOG BONIFICO", color=discord.Color.green(), timestamp=discord.utils.utcnow())
            emb.add_field(name="Mittente", value=interaction.user.mention)
            emb.add_field(name="Destinatario", value=utente.mention)
            # Sistemato: 'importo' corretto in 'ammontare' per evitare l'errore NameError
            emb.add_field(name="Importo", value=f"{ammontare}$") 
            await invia_log_finanziario(interaction.guild, emb)
        except Exception as log_error:
            print(f"[ERROR] Impossibile inviare il log finanziario del bonifico: {log_error}")


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

# ================= EVENTI INIZIALI =================

@bot.event
async def on_ready():
    print(f'{"="*40}')
    print(f'🤖 LOG IN SECONDO BOT: {bot.user}')
    
    # Avvia il loop dei finanziamenti se non è già attivo
    if not controllo_finanziamenti.is_running():
        controllo_finanziamenti.start()
        print("📊 [TASK] Controllo finanziamenti avviato!")
        
    # Avvia il loop di assicurazioni e revisioni (soglia 7gg + DM) se non è già attivo
    if not controllo_scadenze_veicoli.is_running():
        controllo_scadenze_veicoli.start()
        print("🚘 [TASK] Controllo scadenze veicoli (Assicurazioni/Revisioni) avviato!")
    
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
