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
TOKEN = os.environ.get("TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")
RUOLO_STAFF_ID = 1482856659284922530

intents = discord.Intents.default()
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ================= DATABASE SETUP =================

def get_db_connection():
    try:
        url = DATABASE_URL.replace("postgres://", "postgresql://")
        conn = psycopg2.connect(url, sslmode='require', connect_timeout=10)
        return conn
    except Exception as e:
        print(f"❌ Errore connessione DB: {e}")
        return None

def init_db():
    conn = get_db_connection()
    if not conn: 
        return
    cur = conn.cursor()

    # 1. Creazione Tabelle Base (Usa sempre IF NOT EXISTS)
    cur.execute("CREATE TABLE IF NOT EXISTS users (user_id TEXT PRIMARY KEY, wallet INTEGER DEFAULT 20000, bank INTEGER DEFAULT 0)")
    cur.execute("CREATE TABLE IF NOT EXISTS items (name TEXT PRIMARY KEY, description TEXT, price INTEGER, role_required TEXT)")
    cur.execute("CREATE TABLE IF NOT EXISTS inventory (user_id TEXT, item_name TEXT, quantity INTEGER, PRIMARY KEY (user_id, item_name))")
    cur.execute("CREATE TABLE IF NOT EXISTS depositi (role_id TEXT PRIMARY KEY, money INTEGER DEFAULT 0)")
    cur.execute("CREATE TABLE IF NOT EXISTS depositi_items (role_id TEXT, item_name TEXT, quantity INTEGER, PRIMARY KEY (role_id, item_name))")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS fatture (
            id_fattura TEXT PRIMARY KEY,
            id_cliente TEXT,
            id_azienda TEXT,
            descrizione TEXT,
            prezzo INTEGER,
            data TEXT,
            stato TEXT DEFAULT 'Pendente'
        )
    """)

    # 2. Aggiornamento tabelle esistenti (ALTER TABLE)
    # Usiamo blocchi try/except separati per ogni colonna così se una esiste già non blocca l'altra
    
    # Aggiunta ore_lavorate a users
    try:
        cur.execute("ALTER TABLE users ADD COLUMN ore_lavorate REAL DEFAULT 0")
        conn.commit()
    except Exception:
        conn.rollback() # Ignora se la colonna esiste già

    # Aggiunta ruolo a turni
    try:
        cur.execute("ALTER TABLE turni ADD COLUMN ruolo TEXT")
        conn.commit()
    except Exception:
        conn.rollback() # Ignora se la colonna esiste già

def inizializza_db_fatture():
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # Crea la tabella se non esiste con i nomi delle colonne corretti
        cur.execute("""
            CREATE TABLE IF NOT EXISTS fatture (
                id_fattura TEXT PRIMARY KEY,
                id_cliente TEXT NOT NULL,
                id_azienda TEXT NOT NULL,
                descrizione TEXT,
                prezzo BIGINT,
                data TEXT,
                stato TEXT DEFAULT 'Pendente'
            );
        """)
        
        # Questo comando aggiunge la colonna 'stato' se la tabella esiste già ma è vecchia
        cur.execute("""
            DO $$ 
            BEGIN 
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                               WHERE table_name='fatture' AND column_name='stato') THEN 
                    ALTER TABLE fatture ADD COLUMN stato TEXT DEFAULT 'Pendente';
                END IF;
            END $$;
        """)
        
        conn.commit()
        cur.close()
        conn.close()
        print("✅ Database Fatture sincronizzato con successo!")
    except Exception as e:
        print(f"❌ Errore inizializzazione tabella: {e}")

# RICORDA: Nel tuo evento @bot.event async def on_ready():
# aggiungi una riga con: inizializza_db_fatture()


    # Chiudiamo tutto correttamente
    cur.close()
    conn.close()
    print("✅ Database inizializzato correttamente!")

# Chiama la funzione
init_db()

# ================= HELPER FUNCTIONS =================

def get_user_data(user_id):
    conn = get_db_connection()
    if not conn: return {"wallet": 0, "bank": 0}
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT * FROM users WHERE user_id = %s", (str(user_id),))
    user = cur.fetchone()
    if not user:
        cur.execute("INSERT INTO users (user_id, wallet, bank) VALUES (%s, 3500, 0) RETURNING *", (str(user_id),))
        user = cur.fetchone()
        conn.commit()
    cur.close(); conn.close()
    return user

def is_staff(interaction: discord.Interaction):
    return any(role.id == RUOLO_STAFF_ID for role in interaction.user.roles)

async def get_miei_ruoli_fazione(interaction: Interaction):
    conn = get_db_connection()
    if not conn: return []
    cur = conn.cursor()
    cur.execute("SELECT role_id FROM depositi")
    registrati = [r[0] for r in cur.fetchall()]
    cur.close(); conn.close()
    return [r for r in interaction.user.roles if str(r.id) in registrati]

async def cerca_item_smart(interaction: Interaction, nome_input: str, modo="items"):
    conn = get_db_connection()
    cur = conn.cursor()
    if modo == "items":
        cur.execute("SELECT name FROM items WHERE name ILIKE %s", (f"%{nome_input}%",))
    elif modo == "inventory":
        cur.execute("SELECT item_name FROM inventory WHERE user_id = %s AND item_name ILIKE %s", (str(interaction.user.id), f"%{nome_input}%"))
    else:
        role_id = modo.replace("fazione_", "")
        cur.execute("SELECT item_name FROM depositi_items WHERE role_id = %s AND item_name ILIKE %s", (role_id, f"%{nome_input}%"))
    
    risultati = list(set([r[0] for r in cur.fetchall()]))
    cur.close(); conn.close()
    
    if not risultati:
        await interaction.followup.send(f"❌ Nessun oggetto trovato per '{nome_input}'.")
        return None
    if len(risultati) == 1: return risultati[0]

    view = discord.ui.View()
    select = discord.ui.Select(options=[discord.SelectOption(label=n) for n in risultati[:25]])
    
    async def callback(i: Interaction):
        for item in view.children: item.disabled = True
        await i.response.edit_message(view=view)
        view.value = select.values[0]; view.stop()
        
    select.callback = callback
    view.add_item(select); view.value = None
    # Questo messaggio di scelta rimane privato per non intasare, ma l'azione finale sarà pubblica
    await interaction.followup.send("🤔 Più risultati, seleziona quello corretto:", view=view, ephemeral=True)
    await view.wait()
    return view.value
# --- COMANDO INIZIA RACCOLTA ---
@bot.tree.command(name="inizia_raccolta", description="Inizia la raccolta di qualcosa")
@app_commands.describe(cosa="Cosa stai raccogliendo?")
async def inizia_raccolta(interaction: discord.Interaction, cosa: str):
    await interaction.response.defer()
    
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # Registra l'inizio della raccolta (usa l'ora del DB già su Roma)
        cur.execute("""
            INSERT INTO sessioni_raccolta (user_id, cosa_raccoglie, inizio_timestamp)
            VALUES (%s, %s, NOW())
            ON CONFLICT (user_id) DO UPDATE SET
                cosa_raccoglie = EXCLUDED.cosa_raccoglie,
                inizio_timestamp = NOW()
        """, (str(interaction.user.id), cosa))
        
        conn.commit()
        cur.close()
        conn.close()
        
        embed = discord.Embed(
            title="INIZIO RACCOLTA",
            description=f"Hai iniziato la raccolta di: **{cosa}**\nUsa `/finisci_raccolta` per terminare.",
            color=discord.Color.blue()
        )
        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        print(f"Errore inizia_raccolta: {e}")
        await interaction.followup.send("❌ Errore nel database.", ephemeral=True)

# --- COMANDO FINISCI RACCOLTA ---
@bot.tree.command(name="finisci_raccolta", description="Finisci la raccolta di qualcosa")
async def finisci_raccolta(interaction: discord.Interaction):
    await interaction.response.defer()
    
    try:
        conn = get_db_connection()
        from psycopg2.extras import RealDictCursor
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # Recupera dati e calcola i minuti trascorsi
        cur.execute("""
            SELECT cosa_raccoglie, 
            EXTRACT(EPOCH FROM (NOW() - inizio_timestamp)) / 60 AS minuti
            FROM sessioni_raccolta 
            WHERE user_id = %s
        """, (str(interaction.user.id),))
        
        res = cur.fetchone()
        
        if not res:
            cur.close()
            conn.close()
            return await interaction.followup.send("❌ Non hai nessuna raccolta attiva! Usa `/inizia_raccolta`.", ephemeral=True)

        minuti_totali = int(res['minuti'])
        oggetto = res['cosa_raccoglie']
        
        # Elimina la sessione finita
        cur.execute("DELETE FROM sessioni_raccolta WHERE user_id = %s", (str(interaction.user.id),))
        
        conn.commit()
        cur.close()
        conn.close()
        
        # Embed di chiusura
        embed = discord.Embed(
            title="FINE RACCOLTA",
            color=discord.Color.green(),
            timestamp=datetime.datetime.now()
        )
        embed.add_field(name="👷 Cittadino", value=interaction.user.mention, inline=True)
        embed.add_field(name="📦 Raccolto", value=f"**{oggetto}**", inline=True)
        embed.add_field(name="⏱️ Tempo", value=f"**{minuti_totali} minuti**", inline=False)
        
        await interaction.followup.send(content=f"✅ {interaction.user.mention} ha terminato la raccolta.", embed=embed)
        
    except Exception as e:
        print(f"Errore finisci_raccolta: {e}")
        await interaction.followup.send("❌ Errore nel calcolo dei minuti.", ephemeral=True)
@bot.tree.command(name="me", description="Esegui un'azione in gioco (Roleplay)")
@app_commands.describe(azione="Descrivi l'azione che stai compiendo")
async def me(interaction: discord.Interaction, azione: str):
    # Creazione dell'Embed con i parametri richiesti
    embed = discord.Embed(
        title="<a:ciak:1334285912653434993> 𝐀𝐳𝐢𝐨𝐧𝐞  <a:progresso:1334288992547635394>",
        description=f"{interaction.user.mention} : {azione}",
        color=discord.Color.from_rgb(170, 142, 214) # Un viola elegante per le azioni RP
    )
    
    # Invia il messaggio nel canale in cui è stato usato il comando
    await interaction.response.send_message(embed=embed)
# ================= COMANDI BANDI =================

@bot.tree.command(name="bando_aperto", description="Annuncia l'apertura dei bandi")
async def bando_aperto(interaction: Interaction):
    if not any(r.id == RUOLO_STAFF_ID for r in interaction.user.roles) and not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("❌ Non sei autorizzato.", ephemeral=True)
    
    img_url = "https://cdn.discordapp.com/attachments/1483585343176310979/1483857765771378830/1773849369454.png?ex=69bc1dbc&is=69bacc3c&hm=8b8a8caeffb45c276b330eca0c799c4c3565f227cfe63f0277d7f96de3d58052&"
    
    embed = discord.Embed(
        title="📝 BANDI APERTI",
        description="I bandi per entrare a far parte del nostro staff o delle fazioni sono ora **APERTI**!\nInviate la vostra candidatura nei canali appositi.",
        color=discord.Color.blue(),
        timestamp=datetime.datetime.now()
    )
    embed.set_image(url=img_url)
    embed.set_footer(text="Platinum City RP - Reclutamento")
    
    await interaction.response.send_message(content="@everyone", embed=embed)

@bot.tree.command(name="bando_chiuso", description="Annuncia la chiusura dei bandi")
async def bando_chiuso(interaction: Interaction):
    if not any(r.id == RUOLO_STAFF_ID for r in interaction.user.roles) and not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("❌ Non sei autorizzato.", ephemeral=True)
    
    img_url = "https://cdn.discordapp.com/attachments/1483585343176310979/1483857765444227185/17738493694541.png?ex=69bc1dbc&is=69bacc3c&hm=e0adfb4ac0f2a73a3b604412a8e2c620adb9b5a3f083ca46bb08f8bef7812a5d&"
    
    embed = discord.Embed(
        title="🔒 BANDI CHIUSI",
        description="Le candidature sono ufficialmente **CHIUSE**. Grazie a tutti coloro che hanno partecipato!",
        color=discord.Color.dark_grey(),
        timestamp=datetime.datetime.now()
    )
    embed.set_image(url=img_url)
    embed.set_footer(text="Platinum City RP - Reclutamento Terminato")
    
    await interaction.response.send_message(content="@everyone", embed=embed)

@bot.tree.command(name="clear", description="Elimina un numero specifico di messaggi da questo canale")
@app_commands.describe(quantita="Numero di messaggi da eliminare (max 100)")
async def clear(interaction: discord.Interaction, quantita: int):
    # ID del ruolo autorizzato
    ID_RUOLO_AUTORIZZATO = 1482856659284922530
    
    # Controllo se l'utente ha il ruolo richiesto
    role = interaction.guild.get_role(RUOLO_STAFF_ID)
    if role not in interaction.user.roles:
        return await interaction.response.send_message(
            "❌ Non hai i permessi necessari (Staff) per usare questo comando.", 
            ephemeral=True
        )

    # Controllo che la quantità sia valida
    if quantita < 1 or quantita > 100:
        return await interaction.response.send_message(
            "⚠️ Puoi eliminare da 1 a 100 messaggi alla volta.", 
            ephemeral=True
        )

    await interaction.response.defer(ephemeral=True)

    try:
        # Elimina i messaggi
        deleted = await interaction.channel.purge(limit=quantita)
        
        # Crea un embed di conferma
        embed = discord.Embed(
            description=f"✅ Pulizia completata: eliminati **{len(deleted)}** messaggi.",
            color=discord.Color.green()
        )
        
        # Invia la conferma (visibile solo a chi ha usato il comando)
        await interaction.followup.send(embed=embed)
        
    except discord.Forbidden:
        await interaction.followup.send("❌ Il bot non ha i permessi di 'Gestire i messaggi' in questo canale.", ephemeral=True)
    except Exception as e:
        print(f"Errore comando clear: {e}")
        await interaction.followup.send("❌ Si è verificato un errore durante la pulizia.", ephemeral=True)
@bot.tree.command(name="anonimo", description="Invia un messaggio criptato sulla rete segreta")
@app_commands.describe(
    messaggio="Il testo del messaggio segreto",
    nickname="Il tuo alias segreto (obbligatorio solo la prima volta o per cambiarlo)"
)
async def anonimo(interaction: discord.Interaction, messaggio: str, nickname: str = None):
    await interaction.response.defer(ephemeral=True)
    
    try:
        conn = get_db_connection()
        from psycopg2.extras import RealDictCursor
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        cur.execute("SELECT nickname FROM utenti_anonimi WHERE user_id = %s", (str(interaction.user.id),))
        res = cur.fetchone()
        
        if not res and not nickname:
            cur.close()
            conn.close()
            return await interaction.followup.send("❌ Devi specificare un `nickname` la prima volta!", ephemeral=True)
        
        alias_da_usare = nickname if nickname else res['nickname']
        
        if nickname:
            cur.execute("""
                INSERT INTO utenti_anonimi (user_id, nickname)
                VALUES (%s, %s)
                ON CONFLICT (user_id) DO UPDATE SET nickname = EXCLUDED.nickname
            """, (str(interaction.user.id), nickname))
            conn.commit()
            
        cur.close()
        conn.close()
        
        desc_testo = (
            f"```\n"
            f"SISTEMA: Connessione Criptata\n"
            f"MITTENTE: {alias_da_usare}\n"
            f"```\n"
            f"**MESSAGGIO RICEVUTO:**\n"
            f"> {messaggio}"
        )

        embed = discord.Embed(
            title="🔐 █▓▒░ ＥＮＣＲＹＰＴＥＤ ＮＥＴＷＯＲＫ ░▒▓█ 🔐",
            description=desc_testo,
            color=discord.Color.dark_theme(),
            timestamp=datetime.datetime.now()
        )
        embed.set_footer(text="Tracciamento IP: Fallito • Rete Anonima")
        
        await interaction.channel.send(embed=embed)
        await interaction.followup.send("✅ Messaggio inviato in totale anonimato.", ephemeral=True)

    except Exception as e:
        print(f"Errore anonimo: {e}")
        await interaction.followup.send("❌ Errore critico nel sistema di criptazione.", ephemeral=True)


# ================= COMANDI ECONOMIA BASE =================
RUOLO_AUTORIZZATO_ID = 1482856662141370389
@bot.tree.command(name="rp_online", description="Annuncia l'apertura della sessione RP")
@app_commands.describe(id_psn="Inserisci il tuo ID PSN per farti aggiungere")
async def rp_online(interaction: Interaction, id_psn: str):
    # Verifica ruolo specifico
    if not any(r.id == RUOLO_AUTORIZZATO_ID for r in interaction.user.roles) and not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("❌ Non hai il ruolo autorizzato per startare l'RP.", ephemeral=True)
    
    embed = discord.Embed(
        title="🟢 SESSIONE ROLEPLAY ONLINE",
        description="La sessione è ufficialmente **APERTA**! Preparatevi ed entrate in gioco.",
        color=discord.Color.from_rgb(46, 204, 113),
        timestamp=datetime.datetime.now()
    )
    embed.add_field(name="👤 Host / Startato da", value=interaction.user.mention, inline=True)
    embed.add_field(name="🎮 ID PSN Host", value=f"`{id_psn}`", inline=True)
    embed.add_field(name="📢 Come entrare?", value="Per entrare basta fare il modulo ed inviare la richiesta di invito alla crew.", inline=False)
    embed.set_footer(text="City RP - Buon gioco a tutti!")
    if interaction.guild.icon:
        embed.set_thumbnail(url=interaction.guild.icon.url)
    
    await interaction.response.send_message(content="@everyone", embed=embed)

@bot.tree.command(name="rp_offline", description="Annuncia la chiusura della sessione RP")
async def rp_offline(interaction: Interaction):
    # Verifica ruolo specifico
    if not any(r.id == RUOLO_AUTORIZZATO_ID for r in interaction.user.roles) and not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("❌ Non hai il ruolo autorizzato per chiudere l'RP.", ephemeral=True)
    
    embed = discord.Embed(
        title="🔴 SESSIONE ROLEPLAY OFFLINE",
        description="La sessione di Roleplay è stata **CHIUSA**. Grazie a tutti per la partecipazione!",
        color=discord.Color.from_rgb(231, 76, 60),
        timestamp=datetime.datetime.now()
    )
    embed.add_field(name="👤 Chiuso da", value=interaction.user.mention, inline=True)
    embed.add_field(name="⏰ Stato", value="🔴 Offline", inline=True)
    embed.set_footer(text="City RP - Ci vediamo alla prossima sessione!")
    
    await interaction.response.send_message(content="@everyone", embed=embed)
    
@bot.tree.command(name="portafoglio", description="Visualizza il tuo saldo contanti e in banca")
async def portafoglio(interaction: discord.Interaction):
    u = get_user_data(interaction.user.id)
    
    # Creazione dell'Embed
    embed = discord.Embed(
        title="💰 ESTRATTO CONTO PERSONALE",
        color=discord.Color.gold(), # Colore oro per il tema soldi
        timestamp=datetime.datetime.now()
    )
    
    # Imposta l'avatar dell'utente come miniatura a destra
    embed.set_thumbnail(url=interaction.user.display_avatar.url)
    
    # Campi per i saldi (inline=True li mette uno di fianco all'altro)
    embed.add_field(
        name="💵 Contanti (Wallet)", 
        value=f"**{u['wallet']:,}$**", 
        inline=True
    )
    embed.add_field(
        name="💳 Conto Bancario", 
        value=f"**{u['bank']:,}$**", 
        inline=True
    )
    
    # Calcolo del patrimonio totale
    totale = u['wallet'] + u['bank']
    embed.add_field(
        name="📊 Patrimonio Totale", 
        value=f"**{totale:,}$**", 
        inline=False
    )

    embed.set_footer(text=f"Richiesto da {interaction.user.display_name}")

    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="deposita", description="Metti soldi in banca")
async def deposita(interaction: Interaction, importo: int):
    u = get_user_data(interaction.user.id)
    if importo <= 0 or u['wallet'] < importo:
        return await interaction.response.send_message("❌ Importo non valido o contanti insufficienti.")
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("UPDATE users SET wallet = wallet - %s, bank = bank + %s WHERE user_id = %s", (importo, importo, str(interaction.user.id)))
    conn.commit(); cur.close(); conn.close()
    await interaction.response.send_message(f"🏦 **{interaction.user.display_name}** ha depositato **{importo}$** in banca.")

@bot.tree.command(name="preleva", description="Preleva dalla banca")
async def preleva(interaction: Interaction, importo: int):
    u = get_user_data(interaction.user.id)
    if importo <= 0 or u['bank'] < importo:
        return await interaction.response.send_message("❌ Importo non valido o banca vuota.")
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("UPDATE users SET bank = bank - %s, wallet = wallet + %s WHERE user_id = %s", (importo, importo, str(interaction.user.id)))
    conn.commit(); cur.close(); conn.close()
    await interaction.response.send_message(f"💸 **{interaction.user.display_name}** ha prelevato **{importo}$**.")

@bot.tree.command(name="dai_soldi", description="Dai soldi a un altro utente")
async def dai_soldi(interaction: Interaction, utente: discord.Member, importo: int):
    if utente.id == interaction.user.id: return await interaction.response.send_message("❌ Non puoi darti soldi da solo.")
    u = get_user_data(interaction.user.id)
    if importo <= 0 or u['wallet'] < importo: return await interaction.response.send_message("❌ Fondi insufficienti.")
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("UPDATE users SET wallet = wallet - %s WHERE user_id = %s", (importo, str(interaction.user.id)))
    cur.execute("UPDATE users SET wallet = wallet + %s WHERE user_id = %s", (importo, str(utente.id)))
    conn.commit(); cur.close(); conn.close()
    await interaction.response.send_message(f"🤝 **{interaction.user.display_name}** ha dato **{importo}$** a **{utente.mention}**.")
# --- COMANDO PER CREARE IL DOCUMENTO ---
@bot.tree.command(name="crea_documento", description="Registra il tuo documento d'identità")
@app_commands.choices(genere=[
    app_commands.Choice(name="Maschio", value="Maschio"),
    app_commands.Choice(name="Femmina", value="Femmina")
])
async def crea_documento(
    interaction: discord.Interaction, 
    nome: str, 
    cognome: str, 
    data_di_nascita: str, 
    luogo_di_nascita: str, 
    altezza: int, 
    genere: app_commands.Choice[str]
):
    await interaction.response.defer(ephemeral=True)
    
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # Inserisce o aggiorna se esiste già (così uno può rifarsi il documento)
        cur.execute("""
            INSERT INTO documenti (user_id, nome, cognome, data_nascita, luogo_nascita, altezza, genere)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE SET
                nome = EXCLUDED.nome,
                cognome = EXCLUDED.cognome,
                data_nascita = EXCLUDED.data_nascita,
                luogo_nascita = EXCLUDED.luogo_nascita,
                altezza = EXCLUDED.altezza,
                genere = EXCLUDED.genere
        """, (str(interaction.user.id), nome, cognome, data_di_nascita, luogo_di_nascita, altezza, genere.value))
        
        conn.commit()
        cur.close()
        conn.close()
        
        await interaction.followup.send("✅ Documento creato con successo! Usa `/mostra_documento` per vederlo.", ephemeral=True)
        
    except Exception as e:
        print(f"ERRORE CREAZIONE DOCUMENTO: {e}")
        await interaction.followup.send("❌ Errore durante la creazione del documento.", ephemeral=True)

# --- COMANDO PER MOSTRARE IL DOCUMENTO ---
@bot.tree.command(name="mostra_documento", description="Mostra il tuo documento o quello di un altro cittadino")
async def mostra_documento(interaction: discord.Interaction, cittadino: discord.Member = None):
    await interaction.response.defer()
    
    # Se non specifichi un utente, mostra il tuo
    target = cittadino if cittadino else interaction.user
    
    try:
        conn = get_db_connection()
        from psycopg2.extras import RealDictCursor
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        cur.execute("SELECT * FROM documenti WHERE user_id = %s", (str(target.id),))
        doc = cur.fetchone()
        
        cur.close()
        conn.close()
        
        if not doc:
            msg = "Non hai ancora un documento. Crealo con `/crea_documento`!" if target == interaction.user else f"{target.display_name} non ha ancora un documento."
            return await interaction.followup.send(msg)

        # Creazione dell'Embed stile Carta d'Identità
        embed = discord.Embed(
            title="🪪 CARTA D'IDENTITÀ",
            color=discord.Color.dark_red() if doc['genere'] == "Maschio" else discord.Color.magenta()
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(name="Nome", value=doc['nome'], inline=True)
        embed.add_field(name="Cognome", value=doc['cognome'], inline=True)
        embed.add_field(name="Sesso", value=doc['genere'], inline=True)
        embed.add_field(name="Data di Nascita", value=doc['data_nascita'], inline=True)
        embed.add_field(name="Luogo di Nascita", value=doc['luogo_nascita'], inline=True)
        embed.add_field(name="Altezza", value=f"{doc['altezza']} cm", inline=True)
        embed.set_footer(text=f"ID Cittadino: {target.id}")
        
        # Messaggio di Roleplay
        testo_rp = f"***{interaction.user.display_name}** estrae il documento e lo mostra.*"
        await interaction.followup.send(content=testo_rp, embed=embed)
        
    except Exception as e:
        print(f"ERRORE MOSTRA DOCUMENTO: {e}")
        await interaction.followup.send("❌ Errore nel recupero del documento.")
    # --- COMANDO INIZIO TURNO (Ruolo Libero) ---
# --- COMANDO INIZIO TURNO ---
@bot.tree.command(name="inizio_turno", description="Inizia il tuo turno di lavoro")
@app_commands.describe(ruolo="Specifica il tuo ruolo (es. Polizia, Medico, Meccanico)")
async def inizio_turno(interaction: discord.Interaction, ruolo: str):
    await interaction.response.defer()
    
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # Salviamo l'inizio e il ruolo scelto
        cur.execute("""
            INSERT INTO turni (user_id, inizio, ruolo)
            VALUES (%s, NOW(), %s)
            ON CONFLICT (user_id) DO UPDATE SET 
                inizio = NOW(),
                ruolo = EXCLUDED.ruolo
        """, (str(interaction.user.id), ruolo))
        
        conn.commit()
        cur.close()
        conn.close()
        
        embed = discord.Embed(
            title="𝐓𝐮𝐫𝐧𝐨 𝐀𝐯𝐯𝐢𝐚𝐭𝐨",
            description=f"Hai iniziato il servizio come: **{ruolo}**\nOrario d'inizio registrato correttamente.",
            color=discord.Color.blue()
        )
        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        print(f"Errore inizio_turno: {e}")
        await interaction.followup.send("❌ Errore nella registrazione del turno.", ephemeral=True)

# --- COMANDO FINE TURNO ---
@bot.tree.command(name="fine_turno", description="Concludi il tuo turno di lavoro")
async def fine_turno(interaction: discord.Interaction):
    await interaction.response.defer()
    
    try:
        conn = get_db_connection()
        from psycopg2.extras import RealDictCursor
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # Recuperiamo ruolo e calcoliamo i minuti (già sincronizzati su Roma)
        cur.execute("""
            SELECT ruolo, EXTRACT(EPOCH FROM (NOW() - inizio)) / 60 AS minuti
            FROM turni 
            WHERE user_id = %s
        """, (str(interaction.user.id),))
        
        res = cur.fetchone()
        
        if not res:
            cur.close()
            conn.close()
            return await interaction.followup.send("❌ Non hai nessun turno attivo! Usa `/inizio_turno`.", ephemeral=True)

        minuti_totali = int(res['minuti'])
        ruolo_svolto = res['ruolo']
        
        # Cancelliamo il turno dal database
        cur.execute("DELETE FROM turni WHERE user_id = %s", (str(interaction.user.id),))
        
        conn.commit()
        cur.close()
        conn.close()
        
        # Embed finale con il riepilogo
        embed = discord.Embed(
            title="<a:ciak:1334285912653434993> 𝐅𝐢𝐧𝐞 𝐒𝐞𝐫𝐯𝐢𝐳𝐢𝐨",
            color=discord.Color.green(),
            timestamp=datetime.datetime.now()
        )
        embed.add_field(name="👷 Lavoratore", value=interaction.user.mention, inline=True)
        embed.add_field(name="💼 Ruolo", value=f"**{ruolo_svolto}**", inline=True)
        embed.add_field(name="⏱️ Durata", value=f"**{minuti_totali} minuti**", inline=False)
        
        await interaction.followup.send(content=f"✅ {interaction.user.mention} ha terminato il turno di lavoro.", embed=embed)
        
    except Exception as e:
        print(f"Errore fine_turno: {e}")
        await interaction.followup.send("❌ Errore nel calcolo del tempo del turno.", ephemeral=True)

# --- COMANDO PER ELIMINARE IL DOCUMENTO (SOLO ADMIN) ---
@bot.tree.command(name="elimina_documento", description="Elimina il documento di un cittadino (Solo Admin)")
@app_commands.default_permissions(administrator=True) # Rende il comando visibile solo agli admin
@app_commands.describe(cittadino="Il cittadino a cui vuoi cancellare il documento")
async def elimina_documento(interaction: discord.Interaction, cittadino: discord.Member):
    await interaction.response.defer(ephemeral=True)
    
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # Verifichiamo prima se il documento esiste
        cur.execute("SELECT nome, cognome FROM documenti WHERE user_id = %s", (str(cittadino.id),))
        result = cur.fetchone()
        
        if not result:
            cur.close()
            conn.close()
            return await interaction.followup.send(f"❌ Nessun documento trovato per {cittadino.display_name}.", ephemeral=True)
        
        # Eliminazione fisica dalla tabella
        cur.execute("DELETE FROM documenti WHERE user_id = %s", (str(cittadino.id),))
        
        conn.commit()
        cur.close()
        conn.close()
        
        await interaction.followup.send(f"✅ Documento di **{result[0]} {result[1]}** ({cittadino.display_name}) eliminato permanentemente dal database.", ephemeral=True)
        
    except Exception as e:
        print(f"ERRORE ELIMINAZIONE DOCUMENTO: {e}")
        await interaction.followup.send("❌ Errore tecnico durante l'eliminazione.", ephemeral=True)
POLIZIA_ROLE_ID = 1482856748250435918

# --- FUNZIONE DI CONTROLLO POLIZIA ---
def is_polizia(interaction: discord.Interaction):
    return any(role.id == POLIZIA_ROLE_ID for role in interaction.user.roles)

# --- COMANDO /MULTA ---
@bot.tree.command(name="multa", description="Emetti una sanzione a un cittadino")
async def multa(interaction: discord.Interaction, utente: discord.Member, ammontare: int, motivo: str, dipartimento: discord.Role):
    if not is_polizia(interaction):
        return await interaction.response.send_message("❌ Solo i membri della Polizia possono multare!", ephemeral=True)
    
    await interaction.response.defer()
    id_m = ''.join(random.choices(string.ascii_letters + string.digits, k=8))
    data_attuale = datetime.datetime.now().strftime("%d/%m/%Y")

    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO multe (id_multa, user_id, ammontare, id_azienda, motivo, data)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (id_m, str(utente.id), ammontare, str(dipartimento.id), motivo, data_attuale))
        conn.commit()
        cur.close()
        conn.close()

        embed = discord.Embed(title="🚨 Multa Emessa", color=discord.Color.red())
        embed.add_field(name="Cittadino", value=utente.mention, inline=True)
        embed.add_field(name="Importo", value=f"{ammontare}$", inline=True)
        embed.add_field(name="Dipartimento", value=dipartimento.name, inline=True)
        embed.add_field(name="Motivo", value=motivo, inline=False)
        embed.set_footer(text=f"ID Multa: {id_m} | Usa /pagamulta")
        
        await interaction.followup.send(content=f"✅ Multa registrata per {utente.mention}", embed=embed)
    except Exception as e:
        print(f"Errore multa: {e}")
        await interaction.followup.send("❌ Errore nel database.")

# --- COMANDO /PAGAMULTA ---
@bot.tree.command(name="pagamulta", description="Paga le tue sanzioni pendenti")
async def pagamulta(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    try:
        conn = get_db_connection()
        from psycopg2.extras import RealDictCursor
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # Cerchiamo l'ultima multa pendente dell'utente
        cur.execute("SELECT * FROM multe WHERE user_id = %s LIMIT 1", (str(interaction.user.id),))
        multa = cur.fetchone()
        
        if not multa:
            return await interaction.followup.send("✅ Non hai multe da pagare.")

        # Controllo se ha i soldi nel wallet (tabella users)
        cur.execute("SELECT wallet FROM users WHERE user_id = %s", (str(interaction.user.id),))
        user_wallet = cur.fetchone()

        if not user_wallet or user_wallet['wallet'] < multa['ammontare']:
            return await interaction.followup.send(f"❌ Non hai abbastanza contanti! Ti servono {multa['ammontare']}$.")

        # TRANSAZIONE: Scala wallet -> Aggiungi a depositi fazione -> Elimina multa
        cur.execute("UPDATE users SET wallet = wallet - %s WHERE user_id = %s", (multa['ammontare'], str(interaction.user.id)))
        
        cur.execute("""
            INSERT INTO depositi (role_id, money) VALUES (%s, %s)
            ON CONFLICT (role_id) DO UPDATE SET money = depositi.money + EXCLUDED.money
        """, (multa['id_azienda'], multa['ammontare']))
        
        cur.execute("DELETE FROM multe WHERE id_multa = %s", (multa['id_multa'],))
        
        conn.commit()
        cur.close()
        conn.close()

        await interaction.followup.send(f"✅ Hai pagato la multa di {multa['ammontare']}$. I soldi sono andati al dipartimento.")
    except Exception as e:
        print(f"Errore pagamulta: {e}")
        await interaction.followup.send("❌ Errore nel pagamento.")
@bot.tree.command(name="arresto", description="Registra un arresto nel database e annuncialo in chat")
@app_commands.describe(
    utente="Il cittadino da arrestare",
    tempo_minuti="Durata della pena in minuti",
    motivo="Il reato commesso"
)
async def arresto(interaction: discord.Interaction, utente: discord.Member, tempo_minuti: int, motivo: str):
    # Controllo se l'utente è un poliziotto
    if not any(role.id == 1482856748250435918 for role in interaction.user.roles):

        return await interaction.response.send_message("❌ Non hai i permessi per effettuare un arresto.", ephemeral=True)

    await interaction.response.defer()
    
    data_attuale = datetime.datetime.now().strftime("%d/%m/%Y %H:%M")

    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # 1. Salvataggio nel database per la futura /ricerca
        cur.execute("""
            INSERT INTO arresti (user_id, agente_id, motivo, tempo, data)
            VALUES (%s, %s, %s, %s, %s)
        """, (str(utente.id), str(interaction.user.id), motivo, tempo_minuti, data_attuale))
        
        conn.commit()
        cur.close()
        conn.close()

        # 2. Creazione dell'Embed per il Roleplay
        embed = discord.Embed(
            title="⚖️ VERBALE DI ARRESTO",
            color=discord.Color.dark_blue(),
            timestamp=datetime.datetime.now()
        )
        embed.set_thumbnail(url="https://i.imgur.com/8f6uT8R.png") # Opzionale: un'icona della polizia
        
        embed.add_field(name="👤 Detenuto", value=utente.mention, inline=True)
        embed.add_field(name="⏳ Pena", value=f"{tempo_minuti} minuti", inline=True)
        embed.add_field(name="👮 Agente", value=interaction.user.mention, inline=False)
        embed.add_field(name="📝 Motivo", value=motivo, inline=False)
        
        embed.set_footer(text=f"ID Caso registrato nel sistema centrale")

        # Invio del messaggio pubblico
        await interaction.followup.send(
            content=f"🚨 {utente.mention} è stato preso in custodia.",
            embed=embed
        )

    except Exception as e:
        print(f"ERRORE ARRESTO: {e}")
        await interaction.followup.send("❌ Errore durante la registrazione dell'arresto su Supabase.", ephemeral=True)

# --- COMANDI FISICI: AMMANETTA, SMANETTA, ARRESTO ---
@bot.tree.command(name="ammanetta", description="Metti le manette a un cittadino")
async def ammanetta(interaction: discord.Interaction, utente: discord.Member):
    if not is_polizia(interaction):
        return await interaction.response.send_message("❌ Solo la Polizia può usare le manette.", ephemeral=True)
    await interaction.response.send_message(f"🔗 **{interaction.user.display_name}** ha ammanettato **{utente.display_name}**.")

@bot.tree.command(name="smanetta", description="Togli le manette a un cittadino")
async def smanetta(interaction: discord.Interaction, utente: discord.Member):
    if not is_polizia(interaction):
        return await interaction.response.send_message("❌ Non hai le chiavi delle manette.", ephemeral=True)
    await interaction.response.send_message(f"🔓 **{interaction.user.display_name}** ha rimosso le manette a **{utente.display_name}**.")@bot.tree.command(name="arresto", description="Porta un cittadino in cella e registra l'arresto")
async def arresto(interaction: discord.Interaction, utente: discord.Member, tempo_minuti: int, motivo: str):
    if not is_polizia(interaction):
        return await interaction.response.send_message("❌ Non sei un agente.", ephemeral=True)
    
    await interaction.response.defer()
    data_attuale = datetime.datetime.now().strftime("%d/%m/%Y")

    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO arresti (user_id, agente_id, motivo, tempo, data)
            VALUES (%s, %s, %s, %s, %s)
        """, (str(utente.id), str(interaction.user.id), motivo, tempo_minuti, data_attuale))
        conn.commit()
        cur.close()
        conn.close()

        embed = discord.Embed(title="⚖️ Verbale di Arresto", color=discord.Color.dark_blue())
        embed.add_field(name="Detenuto", value=utente.mention, inline=True)
        embed.add_field(name="Tempo", value=f"{tempo_minuti} minuti", inline=True)
        embed.add_field(name="Agente", value=interaction.user.mention, inline=False)
        embed.add_field(name="Motivo", value=motivo, inline=False)
        
        await interaction.followup.send(embed=embed)
    except Exception as e:
        print(f"Errore arresto: {e}")
        await interaction.followup.send("❌ Errore nel salvataggio dell'arresto.")
@bot.tree.command(name="ricerca_cittadino", description="Ricerca avanzata: usa il TAG o NOME e COGNOME")
@app_commands.describe(
    cittadino="Tagga l'utente (opzionale)",
    nome="Nome nel documento (opzionale)",
    cognome="Cognome nel documento (opzionale)"
)
async def ricerca(interaction: discord.Interaction, cittadino: discord.Member = None, nome: str = None, cognome: str = None):
    if not is_polizia(interaction):
        return await interaction.response.send_message("❌ Accesso negato.", ephemeral=True)
    
    await interaction.response.defer()

    target_id = None
    target_member = None

    try:
        conn = get_db_connection()
        from psycopg2.extras import RealDictCursor
        cur = conn.cursor(cursor_factory=RealDictCursor)

        # --- LOGICA DI RICERCA INTELLIGENTE ---
        if cittadino:
            # Caso 1: Ricerca per TAG
            target_id = str(cittadino.id)
            target_member = cittadino
        elif nome and cognome:
            # Caso 2: Ricerca per Nome e Cognome
            cur.execute("""
                SELECT user_id FROM documenti 
                WHERE LOWER(nome) = LOWER(%s) AND LOWER(cognome) = LOWER(%s)
            """, (nome, cognome))
            res_doc = cur.fetchone()
            if res_doc:
                target_id = res_doc['user_id']
                # Proviamo a recuperare il membro dal server per l'avatar, se c'è
                target_member = interaction.guild.get_member(int(target_id))
            else:
                cur.close()
                conn.close()
                return await interaction.followup.send(f"❌ Nessun cittadino trovato con il nome: **{nome} {cognome}**.")
        else:
            cur.close()
            conn.close()
            return await interaction.followup.send("⚠️ Devi taggare qualcuno o inserire sia Nome che Cognome!")

        # --- RECUPERO DATI DAL FASCICOLO ---
        # 1. Dati Documento
        cur.execute("SELECT * FROM documenti WHERE user_id = %s", (target_id,))
        doc = cur.fetchone()
        
        # 2. Veicoli (Recupera TUTTI i veicoli)
        cur.execute("SELECT targa, modello FROM veicoli WHERE owner_id = %s", (target_id,))
        veicoli = cur.fetchall()
        
        # 3. Multe Pendenti
        cur.execute("SELECT * FROM multe WHERE user_id = %s", (target_id,))
        multe = cur.fetchall()
        
        # 4. Storico Arresti (ultimi 5)
        cur.execute("SELECT * FROM arresti WHERE user_id = %s ORDER BY id_arresto DESC LIMIT 5", (target_id,))
        arresti = cur.fetchall()
        
        cur.close()
        conn.close()

        # --- COSTRUZIONE EMBED ---
        embed = discord.Embed(
            title=f"📁 FASCICOLO FEDERALE",
            color=discord.Color.dark_blue(),
            timestamp=datetime.datetime.now()
        )
        
        # Gestione Avatar e Titolo
        nome_display = f"{doc['nome']} {doc['cognome']}" if doc else (target_member.display_name if target_member else "Sconosciuto")
        embed.description = f"**Soggetto:** {nome_display}\n**ID Discord:** `{target_id}`"
        
        if target_member:
            embed.set_thumbnail(url=target_member.display_avatar.url)

        # Sezione Anagrafica
        if doc:
            embed.add_field(name="🪪 Dati Anagrafici", 
                value=f"**Nascita:** {doc['data_nascita']} ({doc['luogo_nascita']})\n**Sesso:** {doc['genere']} | **H:** {doc['altezza']}cm", 
                inline=False)
        else:
            embed.add_field(name="🪪 Dati Anagrafici", value="⚠️ Documento non registrato.", inline=False)

        # Sezione Veicoli
        if veicoli:
            lista_v = "\n".join([f"• `{v['targa']}` - {v['modello']}" for v in veicoli])
            embed.add_field(name="🚘 Veicoli Intestati", value=lista_v, inline=False)
        else:
            embed.add_field(name="🚘 Veicoli Intestati", value="Nessun veicolo registrato.", inline=False)

        # Sezione Multe
        if multe:
            lista_m = "\n".join([f"• **{m['ammontare']}$** - {m['motivo']} ({m['data']})" for m in multe])
            embed.add_field(name="⚠️ Multe Pendenti", value=lista_m, inline=False)
        else:
            embed.add_field(name="⚠️ Multe Pendenti", value="Nessuna multa.", inline=False)

        # Sezione Arresti
        if arresti:
            lista_a = "\n".join([f"• {a['data']}: {a['motivo']} ({a['tempo']} min)" for a in arresti])
            embed.add_field(name="🚔 Cronologia Arresti", value=lista_a, inline=False)
        else:
            embed.add_field(name="🚔 Cronologia Arresti", value="Incensurato.", inline=False)

        await interaction.followup.send(embed=embed)

    except Exception as e:
        print(f"Errore ricerca intelligente: {e}")
        await interaction.followup.send("❌ Errore durante l'interrogazione del database.")




# ================= COMANDI INVENTARIO =================

@bot.tree.command(name="inventario", description="Mostra i tuoi oggetti")
async def inventario(interaction: Interaction):
    await interaction.response.defer()
    conn = get_db_connection(); cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT item_name, quantity FROM inventory WHERE user_id = %s", (str(interaction.user.id),))
    items = cur.fetchall()
    cur.close(); conn.close()
    emb = discord.Embed(title=f"🎒 Zaino di {interaction.user.display_name}", color=discord.Color.blue())
    desc = "\n".join([f"📦 **{i['item_name']}** x{i['quantity']}" for i in items]) if items else "*Vuoto.*"
    emb.description = desc
    await interaction.followup.send(embed=emb)

@bot.tree.command(name="dai_item", description="Dai un oggetto a un utente")
async def dai_item(interaction: Interaction, utente: discord.Member, nome: str, quantita: int = 1):
    if utente.id == interaction.user.id: return await interaction.response.send_message("❌ Impossibile.")
    await interaction.response.defer()
    nome_e = await cerca_item_smart(interaction, nome, "inventory")
    if not nome_e: return
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT quantity FROM inventory WHERE user_id = %s AND item_name = %s", (str(interaction.user.id), nome_e))
    res = cur.fetchone()
    if not res or res[0] < quantita: return await interaction.followup.send("❌ Non ne hai abbastanza.")
    cur.execute("UPDATE inventory SET quantity = quantity - %s WHERE user_id = %s AND item_name = %s", (quantita, str(interaction.user.id), nome_e))
    cur.execute("INSERT INTO inventory (user_id, item_name, quantity) VALUES (%s, %s, %s) ON CONFLICT (user_id, item_name) DO UPDATE SET quantity = inventory.quantity + %s", (str(utente.id), nome_e, quantita, quantita))
    cur.execute("DELETE FROM inventory WHERE quantity <= 0")
    conn.commit(); cur.close(); conn.close()
    await interaction.followup.send(f"📦 **{interaction.user.display_name}** ha passato {quantita}x **{nome_e}** a **{utente.mention}**.")

@bot.tree.command(name="usa", description="Usa un oggetto")
async def usa(interaction: Interaction, nome: str):
    await interaction.response.defer()
    nome_e = await cerca_item_smart(interaction, nome, "inventory")
    if not nome_e: return
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("UPDATE inventory SET quantity = quantity - 1 WHERE user_id = %s AND item_name = %s", (str(interaction.user.id), nome_e))
    cur.execute("DELETE FROM inventory WHERE quantity <= 0")
    conn.commit(); cur.close(); conn.close()
    await interaction.followup.send(f"✨ **{interaction.user.display_name}** ha usato **{nome_e}**!")
# 1. DEFINIZIONE DELLA CLASSE (Deve stare sopra il comando)
# ==========================================
# 1. CLASSE PER IL PAGAMENTO (PagaFatturaView)
# ==========================================
class PagaFatturaView(discord.ui.View):
    def __init__(self, user_id, fatture):
        super().__init__(timeout=180)
        self.user_id = user_id
        
        options = []
        for f in fatture:
            # Salviamo: ID Fattura | Prezzo | ID Azienda (Ruolo)
            options.append(discord.SelectOption(
                label=f"Fattura {f['id_fattura']}",
                description=f"Importo: {f['prezzo']}$",
                value=f"{f['id_fattura']}|{f['prezzo']}|{f['id_azienda']}"
            ))
            
        self.select = discord.ui.Select(placeholder="Scegli la fattura da saldare...", options=options)
        self.select.callback = self.select_callback
        self.add_item(self.select)

    async def select_callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        # Spacchettiamo i dati
        data = self.select.values[0].split('|')
        id_f = data[0]
        prezzo = int(data[1])
        id_azienda = data[2] # Questo è l'ID numerico del ruolo

        try:
            conn = get_db_connection()
            from psycopg2.extras import RealDictCursor
            cur = conn.cursor(cursor_factory=RealDictCursor)
            
            # 1. Controllo se il cittadino ha i soldi nel wallet
            cur.execute("SELECT wallet FROM users WHERE user_id = %s", (str(interaction.user.id),))
            user_data = cur.fetchone()
            
            if not user_data or user_data['wallet'] < prezzo:
                cur.close()
                conn.close()
                return await interaction.followup.send("❌ Non hai abbastanza contanti nel wallet!", ephemeral=True)

            # --- TRANSAZIONE ECONOMICA ---
            # A. Sottrazione soldi al cittadino
            cur.execute("UPDATE users SET wallet = wallet - %s WHERE user_id = %s", (prezzo, str(interaction.user.id)))
            
            # B. Accredito nel deposito fazione (Usa l'ID del ruolo)
            cur.execute("""
                INSERT INTO depositi (role_id, money) 
                VALUES (%s, %s) 
                ON CONFLICT (role_id) 
                DO UPDATE SET money = depositi.money + EXCLUDED.money
            """, (str(id_azienda), prezzo))
            
            # C. Aggiornamento stato fattura
            cur.execute("UPDATE fatture SET stato = 'Pagata' WHERE id_fattura = %s", (id_f,))
            
            conn.commit()
            cur.close()
            conn.close()

            self.select.disabled = True
            await interaction.edit_original_response(
                content=f"✅ Fattura `{id_f}` pagata! **{prezzo}$** accreditati nel deposito fazione.", 
                view=self
            )

        except Exception as e:
            print(f"ERRORE SQL PAGAMENTO: {e}")
            await interaction.followup.send("❌ Errore durante il trasferimento dei fondi.", ephemeral=True)

# ==========================================
# 2. COMANDO PER EMETTERE FATTURA (/fattura)
# ==========================================
@bot.tree.command(name="fattura", description="Emetti una fattura a un cittadino")
async def fattura(interaction: discord.Interaction, cliente: discord.Member, azienda: discord.Role, descrizione: str, prezzo: int):
    await interaction.response.defer()
    
    id_f = ''.join(random.choices(string.ascii_letters + string.digits, k=10))
    data_attuale = datetime.datetime.now().strftime("%d/%m/%Y")
    
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # IMPORTANTE: Salviamo azienda.id (stringa) per matchare la tabella depositi
        cur.execute("""
            INSERT INTO fatture (id_fattura, id_cliente, id_azienda, descrizione, prezzo, data, stato) 
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (id_f, str(cliente.id), str(azienda.id), descrizione, prezzo, data_attuale, 'Pendente'))
        
        conn.commit()
        cur.close()
        conn.close()

        embed = discord.Embed(title="📑 Fattura Emessa", color=discord.Color.gold())
        embed.add_field(name="🏢 Azienda Emittente", value=azienda.mention, inline=True)
        embed.add_field(name="👤 Cliente", value=cliente.mention, inline=True)
        embed.add_field(name="💰 Importo", value=f"**{prezzo}$**", inline=True)
        embed.add_field(name="📝 Causale", value=descrizione, inline=False)
        embed.set_footer(text=f"ID Unico: {id_f}")
        
        await interaction.followup.send(embed=embed)

    except Exception as e:
        print(f"ERRORE SQL FATTURA: {e}")
        await interaction.followup.send("❌ Errore nel salvataggio della fattura.", ephemeral=True)

# ==========================================
# 3. COMANDO PER VISUALIZZARE FATTURE (/pagafattura)
# ==========================================
@bot.tree.command(name="pagafattura", description="Paga le tue fatture pendenti")
async def pagafattura(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    try:
        conn = get_db_connection()
        from psycopg2.extras import RealDictCursor
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM fatture WHERE id_cliente = %s AND stato = 'Pendente'", (str(interaction.user.id),))
        mie_fatture = cur.fetchall()
        cur.close()
        conn.close()

        if not mie_fatture:
            return await interaction.followup.send("✅ Non hai fatture in sospeso.", ephemeral=True)

        view = PagaFatturaView(interaction.user.id, mie_fatture)
        await interaction.followup.send("Seleziona la fattura da pagare:", view=view, ephemeral=True)
    except Exception as e:
        print(f"ERRORE CARICAMENTO: {e}")
        await interaction.followup.send("❌ Errore nel caricamento dei dati.", ephemeral=True)



ID_RUOLO_CONCESSIONARIO = 1482856794639433830

@bot.tree.command(name="registra_veicolo", description="Registra la vendita e salva i dati nel database motorizzazione")
@app_commands.checks.has_any_role(ID_RUOLO_CONCESSIONARIO)
async def registra_veicolo(
    interaction: discord.Interaction, 
    acquirente: discord.Member, 
    marca_modello: str, 
    targa: str, 
    concessionaria: discord.Role
):
    await interaction.response.defer()

    data_ora = datetime.datetime.now().strftime("%d/%m/%Y %H:%M")
    targa_maiuscola = targa.upper().replace(" ", "") # Puliamo la targa da spazi
    nome_item_chiavi = f"<:emoji_2:1464729413651534029> | Chiavi {marca_modello} [{targa_maiuscola}]"

    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # 1. SALVATAGGIO NELLA MOTORIZZAZIONE (Tabella veicoli)
        # Usiamo ON CONFLICT così se la targa esiste già (es. auto usata rivenduta), aggiorna il proprietario
        cur.execute("""
            INSERT INTO veicoli (targa, modello, owner_id, data_vendita) 
            VALUES (%s, %s, %s, %s) 
            ON CONFLICT (targa) 
            DO UPDATE SET 
                owner_id = EXCLUDED.owner_id,
                modello = EXCLUDED.modello,
                data_vendita = EXCLUDED.data_vendita
        """, (targa_maiuscola, marca_modello, str(acquirente.id), data_ora))

        # 2. AGGIUNTA CHIAVI NELL'INVENTARIO (Tabella inventory)
        cur.execute("""
            INSERT INTO inventory (user_id, item_name, quantity) 
            VALUES (%s, %s, 1) 
            ON CONFLICT (user_id, item_name) 
            DO UPDATE SET quantity = inventory.quantity + 1
        """, (str(acquirente.id), nome_item_chiavi))
        
        conn.commit()
        cur.close()
        conn.close()

        # 3. Embed del Contratto
        embed = discord.Embed(title="📝 CONTRATTO DI VENDITA", color=discord.Color.green())
        embed.add_field(name="🏛️ CONCESSIONARIA", value=concessionaria.mention, inline=True)
        embed.add_field(name="👤 ACQUIRENTE", value=f"{acquirente.mention}\nID: `{acquirente.id}`", inline=True)
        embed.add_field(name="🚘 VEICOLO", value=f"**Modello:** {marca_modello}\n**Targa:** `{targa_maiuscola}`", inline=False)
        embed.set_footer(text=f"Registrato in Motorizzazione il {data_ora}")
        
        await interaction.followup.send(content=f"✅ Vendita completata! Veicolo registrato a nome di {acquirente.mention}.", embed=embed)

    except Exception as e:
        print(f"Errore registrazione veicolo: {e}")
        await interaction.followup.send("❌ Errore durante la registrazione nel database.", ephemeral=True)

@bot.tree.command(name="ricerca_targa", description="Interroga il database motorizzazione tramite targa")
async def ricerca_targa(interaction: discord.Interaction, targa: str):
    if not is_polizia(interaction): # Usa la tua funzione di controllo polizia
        return await interaction.response.send_message("❌ Accesso negato.", ephemeral=True)
    
    await interaction.response.defer()
    targa_clean = targa.upper().replace(" ", "")

    try:
        conn = get_db_connection()
        from psycopg2.extras import RealDictCursor
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # Uniamo i dati di veicoli e documenti in una sola query
        cur.execute("""
            SELECT v.targa, v.modello, v.owner_id, d.nome, d.cognome 
            FROM veicoli v
            LEFT JOIN documenti d ON v.owner_id = d.user_id
            WHERE v.targa = %s
        """, (targa_clean,))
        
        res = cur.fetchone()
        cur.close()
        conn.close()

        if not res:
            return await interaction.followup.send(f"⚠️ La targa `{targa_clean}` non risulta nel database.")

        embed = discord.Embed(title="🔍 Risultato Ricerca Targa", color=discord.Color.blue())
        embed.add_field(name="🚘 Veicolo", value=f"**Modello:** {res['modello']}\n**Targa:** `{res['targa']}`", inline=False)
        
        proprietario = f"{res['nome']} {res['cognome']}" if res['nome'] else "Documento non registrato"
        embed.add_field(name="👤 Proprietario", value=f"**Nome:** {proprietario}\n**Menzione:** <@{res['owner_id']}>", inline=False)
        
        await interaction.followup.send(embed=embed)

    except Exception as e:
        print(f"Errore: {e}")
        await interaction.followup.send("❌ Errore nella ricerca.")



# ================= COMANDI FAZIONE =================

@bot.tree.command(name="deposito_fazione", description="Visualizza il deposito di fazione")
async def deposito_fazione(interaction: Interaction):
    await interaction.response.defer()
    miei_ruoli = await get_miei_ruoli_fazione(interaction)
    if not miei_ruoli: return await interaction.followup.send("❌ Non sei in una fazione.")

    async def mostra(inter, rid):
        conn = get_db_connection(); cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT money FROM depositi WHERE role_id = %s", (rid,))
        m = cur.fetchone()['money']
        cur.execute("SELECT item_name, quantity FROM depositi_items WHERE role_id = %s", (rid,))
        it = cur.fetchall()
        r_obj = inter.guild.get_role(int(rid))
        emb = discord.Embed(title=f"🏦 Deposito {r_obj.name}", color=discord.Color.dark_blue())
        emb.add_field(name="Soldi", value=f"{m}$", inline=False)
        lista = "\n".join([f"📦 {i['item_name']} x{i['quantity']}" for i in it]) if it else "Vuoto"
        emb.add_field(name="Oggetti", value=lista, inline=False)
        await inter.followup.send(embed=emb); cur.close(); conn.close()

    if len(miei_ruoli) == 1: await mostra(interaction, str(miei_ruoli[0].id))
    else:
        view = discord.ui.View()
        sel = discord.ui.Select(options=[discord.SelectOption(label=r.name, value=str(r.id)) for r in miei_ruoli])
        async def call(i): 
            for it in view.children: it.disabled = True
            await i.response.edit_message(view=view); await mostra(i, sel.values[0])
        sel.callback = call; view.add_item(sel)
        await interaction.followup.send("Quale deposito vuoi aprire?", view=view, ephemeral=True)

@bot.tree.command(name="deposita_soldi_fazione", description="Deposita soldi in fazione")
async def deposita_soldi_fazione(interaction: Interaction, importo: int):
    await interaction.response.defer()
    miei_ruoli = await get_miei_ruoli_fazione(interaction)
    if not miei_ruoli: return await interaction.followup.send("❌ No Fazione.")
    u = get_user_data(interaction.user.id)
    if importo <= 0 or u['wallet'] < importo: return await interaction.followup.send("❌ Fondi insufficienti.")

    async def procedi(inter, rid):
        conn = get_db_connection(); cur = conn.cursor()
        cur.execute("UPDATE users SET wallet = wallet - %s WHERE user_id = %s", (importo, str(inter.user.id)))
        cur.execute("UPDATE depositi SET money = money + %s WHERE role_id = %s", (importo, rid))
        conn.commit(); cur.close(); conn.close()
        r_obj = inter.guild.get_role(int(rid))
        await inter.followup.send(f"✅ **{inter.user.display_name}** ha depositato **{importo}$** in **{r_obj.name}**.")

    if len(miei_ruoli) == 1: await procedi(interaction, str(miei_ruoli[0].id))
    else:
        view = discord.ui.View()
        sel = discord.ui.Select(options=[discord.SelectOption(label=r.name, value=str(r.id)) for r in miei_ruoli])
        async def call(i): 
            for it in view.children: it.disabled = True
            await i.response.edit_message(view=view); await procedi(i, sel.values[0])
        sel.callback = call; view.add_item(sel)
        await interaction.followup.send("In quale fazione depositi?", view=view, ephemeral=True)

@bot.tree.command(name="preleva_soldi_fazione", description="Preleva soldi dalla fazione")
async def preleva_soldi_fazione(interaction: Interaction, importo: int):
    await interaction.response.defer()
    miei_ruoli = await get_miei_ruoli_fazione(interaction)
    if not miei_ruoli: return await interaction.followup.send("❌ No Fazione.")

    async def procedi(inter, rid):
        conn = get_db_connection(); cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT money FROM depositi WHERE role_id = %s", (rid,))
        if cur.fetchone()['money'] < importo: return await inter.followup.send("❌ Fondo fazione insufficiente.")
        cur.execute("UPDATE depositi SET money = money - %s WHERE role_id = %s", (importo, rid))
        cur.execute("UPDATE users SET wallet = wallet + %s WHERE user_id = %s", (importo, str(inter.user.id)))
        conn.commit(); cur.close(); conn.close()
        r_obj = inter.guild.get_role(int(rid))
        await inter.followup.send(f"💸 **{inter.user.display_name}** ha prelevato **{importo}$** da **{r_obj.name}**.")

    if len(miei_ruoli) == 1: await procedi(interaction, str(miei_ruoli[0].id))
    else:
        view = discord.ui.View()
        sel = discord.ui.Select(options=[discord.SelectOption(label=r.name, value=str(r.id)) for r in miei_ruoli])
        async def call(i):
            for it in view.children: it.disabled = True
            await i.response.edit_message(view=view); await procedi(i, sel.values[0])
        sel.callback = call; view.add_item(sel)
        await interaction.followup.send("Da quale fazione prelevi?", view=view, ephemeral=True)

@bot.tree.command(name="deposita_item_fazione", description="Metti un item in fazione")
async def deposita_item_fazione(interaction: Interaction, nome: str, quantita: int = 1):
    await interaction.response.defer()
    miei_ruoli = await get_miei_ruoli_fazione(interaction)
    if not miei_ruoli: return await interaction.followup.send("❌ No Fazione.")
    nome_e = await cerca_item_smart(interaction, nome, "inventory")
    if not nome_e: return

    async def procedi(inter, rid):
        conn = get_db_connection(); cur = conn.cursor()
        cur.execute("UPDATE inventory SET quantity = quantity - %s WHERE user_id = %s AND item_name = %s", (quantita, str(inter.user.id), nome_e))
        cur.execute("INSERT INTO depositi_items (role_id, item_name, quantity) VALUES (%s, %s, %s) ON CONFLICT (role_id, item_name) DO UPDATE SET quantity = depositi_items.quantity + %s", (rid, nome_e, quantita, quantita))
        cur.execute("DELETE FROM inventory WHERE quantity <= 0")
        conn.commit(); cur.close(); conn.close()
        r_obj = inter.guild.get_role(int(rid))
        await inter.followup.send(f"✅ **{inter.user.display_name}** ha messo {quantita}x **{nome_e}** in **{r_obj.name}**.")

    if len(miei_ruoli) == 1: await procedi(interaction, str(miei_ruoli[0].id))
    else:
        view = discord.ui.View()
        sel = discord.ui.Select(options=[discord.SelectOption(label=r.name, value=str(r.id)) for r in miei_ruoli])
        async def call(i):
            for it in view.children: it.disabled = True
            await i.response.edit_message(view=view); await procedi(i, sel.values[0])
        sel.callback = call; view.add_item(sel)
        await interaction.followup.send("In quale magazzino depositi?", view=view, ephemeral=True)

@bot.tree.command(name="preleva_item_fazione", description="Preleva un item dalla fazione")
async def preleva_item_fazione(interaction: Interaction, nome: str, quantita: int = 1):
    await interaction.response.defer()
    miei_ruoli = await get_miei_ruoli_fazione(interaction)
    if not miei_ruoli: return await interaction.followup.send("❌ No Fazione.")

    async def procedi(inter, rid):
        nome_e = await cerca_item_smart(inter, nome, f"fazione_{rid}")
        if not nome_e: return
        conn = get_db_connection(); cur = conn.cursor()
        cur.execute("SELECT quantity FROM depositi_items WHERE role_id = %s AND item_name = %s", (rid, nome_e))
        res = cur.fetchone()
        if not res or res[0] < quantita: return await inter.followup.send("❌ Magazzino fazione insufficiente.")
        cur.execute("UPDATE depositi_items SET quantity = quantity - %s WHERE role_id = %s AND item_name = %s", (quantita, rid, nome_e))
        cur.execute("INSERT INTO inventory (user_id, item_name, quantity) VALUES (%s, %s, %s) ON CONFLICT (user_id, item_name) DO UPDATE SET quantity = inventory.quantity + %s", (str(inter.user.id), nome_e, quantita, quantita))
        cur.execute("DELETE FROM depositi_items WHERE quantity <= 0")
        conn.commit(); cur.close(); conn.close()
        r_obj = inter.guild.get_role(int(rid))
        await inter.followup.send(f"📦 **{inter.user.display_name}** ha prelevato {quantita}x **{nome_e}** da **{r_obj.name}**.")

    if len(miei_ruoli) == 1: await procedi(interaction, str(miei_ruoli[0].id))
    else:
        view = discord.ui.View()
        sel = discord.ui.Select(options=[discord.SelectOption(label=r.name, value=str(r.id)) for r in miei_ruoli])
        async def call(i):
            for it in view.children: it.disabled = True
            await i.response.edit_message(view=view); await procedi(i, sel.values[0])
        sel.callback = call; view.add_item(sel)
        await interaction.followup.send("Da quale magazzino prelevi?", view=view, ephemeral=True)

# ================= SHOP & LAVORO =================
class LeaderboardPagination(discord.ui.View):
    def __init__(self, data, per_page=10):
        super().__init__(timeout=60)
        self.data = data
        self.per_page = per_page
        self.current_page = 0
        self.total_pages = (len(data) - 1) // per_page + 1

    def create_embed(self, bot):
        start = self.current_page * self.per_page
        end = start + self.per_page
        page_data = self.data[start:end]

        embed = discord.Embed(
            title="🏆 Classifica Ricchezza Globale",
            color=discord.Color.gold()
        )
        
        description = ""
        for i, row in enumerate(page_data, start=start + 1):
            user_id = int(row['user_id'])
            user = bot.get_user(user_id)
            user_name = user.display_name if user else f"Cittadino ({user_id})"
            
            if i == 1: medal = "🥇"
            elif i == 2: medal = "🥈"
            elif i == 3: medal = "🥉"
            else: medal = f"**{i}.**"

            description += f"{medal} **{user_name}**: {row['totale']:,}$\n"
            description += f"└─ *Wallet: {row['wallet']:,}$ | Banca: {row['bank']:,}$*\n\n"

        embed.description = description
        embed.set_footer(text=f"Pagina {self.current_page + 1} di {self.total_pages} • Totale utenti: {len(self.data)}")
        return embed

    @discord.ui.button(label="⬅️ Indietro", style=discord.ButtonStyle.gray)
    async def previous_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page > 0:
            self.current_page -= 1
            await interaction.response.edit_message(embed=self.create_embed(interaction.client), view=self)
        else:
            await interaction.response.send_message("Sei già sulla prima pagina!", ephemeral=True)

    @discord.ui.button(label="Avanti ➡️", style=discord.ButtonStyle.gray)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page < self.total_pages - 1:
            self.current_page += 1
            await interaction.response.edit_message(embed=self.create_embed(interaction.client), view=self)
        else:
            await interaction.response.send_message("Sei già sull'ultima pagina!", ephemeral=True)

@bot.tree.command(name="leaderboard", description="Mostra la classifica completa sfogliabile")
async def leaderboard(interaction: discord.Interaction):
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    # Prendiamo tutti i dati ordinati per ricchezza totale
    cur.execute("SELECT user_id, wallet, bank, (wallet + bank) AS totale FROM users ORDER BY totale DESC")
    all_users = cur.fetchall()
    cur.close(); conn.close()

    if not all_users:
        return await interaction.response.send_message("📭 Database vuoto.")

    view = LeaderboardPagination(all_users)
    # Passiamo bot (client) per recuperare i nomi degli utenti
    embed = view.create_embed(interaction.client)
    
    await interaction.response.send_message(embed=embed, view=view)
@bot.tree.command(name="shop", description="Mostra il catalogo")
async def shop(interaction: Interaction):
    conn = get_db_connection(); cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT * FROM items")
    items = cur.fetchall()
    cur.close(); conn.close()
    emb = discord.Embed(title="🛒 Negozio", color=discord.Color.green())
    for i in items:
        req = "Nessuno" if i['role_required'] == "None" else f"<@&{i['role_required']}>"
        emb.add_field(name=i['name'], value=f"Prezzo: {i['price']}$\nReq: {req}\n{i['description']}", inline=False)
    await interaction.response.send_message(embed=emb)

@bot.tree.command(name="compra", description="Compra un oggetto")
async def compra(interaction: Interaction, nome: str, quantita: int = 1):
    await interaction.response.defer()
    nome_e = await cerca_item_smart(interaction, nome, "items")
    if not nome_e: return
    conn = get_db_connection(); cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT * FROM items WHERE name = %s", (nome_e,))
    item = cur.fetchone()
    u = get_user_data(interaction.user.id)
    prezzo_totale = item['price'] * quantita
    if item['role_required'] != "None" and not any(str(r.id) == item['role_required'] for r in interaction.user.roles):
        return await interaction.followup.send("❌ Grado fazione mancante.")
    if u['wallet'] < prezzo_totale: return await interaction.followup.send("❌ Soldi insufficienti.")
    cur.execute("UPDATE users SET wallet = wallet - %s WHERE user_id = %s", (prezzo_totale, str(interaction.user.id)))
    cur.execute("INSERT INTO inventory (user_id, item_name, quantity) VALUES (%s, %s, %s) ON CONFLICT (user_id, item_name) DO UPDATE SET quantity = inventory.quantity + %s", (str(interaction.user.id), nome_e, quantita, quantita))
    conn.commit(); cur.close(); conn.close()
    await interaction.followup.send(f"🛍️ **{interaction.user.display_name}** ha comprato {quantita}x **{nome_e}**!")

@bot.tree.command(name="cerca", description="Cerca materiali (1 min)")
async def cerca(interaction: Interaction):
    await interaction.response.send_message(f"🔍 **{interaction.user.display_name}** ha iniziato a cercare materiali... torna tra 1 minuto.")
    await asyncio.sleep(60)
    mat = random.choice(["Ferro", "Rame", "Plastica", "Legno", "Pezzi di Vetro", "Cavi Elettrici"])
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("INSERT INTO inventory (user_id, item_name, quantity) VALUES (%s, %s, 1) ON CONFLICT (user_id, item_name) DO UPDATE SET quantity = inventory.quantity + 1", (str(interaction.user.id), mat))
    conn.commit(); cur.close(); conn.close()
    await interaction.channel.send(f"✅ **{interaction.user.mention}** ha trovato: **{mat}**!")

# --- CLASSE VIEW PER I BOTTONI ---

# --- CLASSE VIEW PER I BOTTONI (Corretta e Reattiva) ---
class BlackjackView(discord.ui.View):
    def __init__(self, interaction, somma, mano_p, mano_b):
        super().__init__(timeout=60) # Il gioco scade dopo 60 secondi di inattività
        self.interaction = interaction
        self.somma = somma
        self.mano_p = mano_p
        self.mano_b = mano_b

    def get_tot(self, mano):
        tot = sum(mano)
        as_count = mano.count(11)
        while tot > 21 and as_count > 0:
            tot -= 10
            as_count -= 1
        return tot

    @discord.ui.button(label="Carta 🃏", style=discord.ButtonStyle.green)
    async def carta(self, inter: discord.Interaction, button: discord.ui.Button):
        # Controllo che solo chi ha iniziato la partita possa giocare
        if inter.user.id != self.interaction.user.id:
            return await inter.response.send_message("❌ Questa non è la tua partita!", ephemeral=True)
        
        self.mano_p.append(random.randint(2, 11))
        
        if self.get_tot(self.mano_p) > 21:
            await self.concludi(inter, "sballato")
        else:
            await self.update_msg(inter)

    @discord.ui.button(label="Stai ✋", style=discord.ButtonStyle.red)
    async def stai(self, inter: discord.Interaction, button: discord.ui.Button):
        if inter.user.id != self.interaction.user.id:
            return await inter.response.send_message("❌ Questa non è la tua partita!", ephemeral=True)
        
        # Logica del Banco
        while self.get_tot(self.mano_b) < 17:
            self.mano_b.append(random.randint(2, 11))
        
        tot_p = self.get_tot(self.mano_p)
        tot_b = self.get_tot(self.mano_b)
        
        if tot_b > 21 or tot_p > tot_b:
            esito = "vinto"
        elif tot_p < tot_b:
            esito = "perso"
        else:
            esito = "pareggio"
            
        await self.concludi(inter, esito)

    async def update_msg(self, inter):
        # Usiamo edit_message per aggiornare l'interfaccia senza inviare nuovi messaggi
        emb = discord.Embed(title="🃏 Blackjack - In Corso", color=discord.Color.gold())
        emb.add_field(name="La tua mano 👤", value=f"{self.mano_p}\n**Totale: {self.get_tot(self.mano_p)}**", inline=True)
        emb.add_field(name="Banco 🏛️", value=f"[{self.mano_b[0]}, ?]\n**Totale: ?**", inline=True)
        emb.set_footer(text=f"Puntata: {self.somma}$")
        await inter.response.edit_message(embed=emb, view=self)

    async def concludi(self, inter, esito):
        self.stop() # Disattiva i bottoni immediatamente
        tot_p = self.get_tot(self.mano_p)
        tot_b = self.get_tot(self.mano_b)
        
        # Connessione al database per pagare/sottrarre
        conn = get_db_connection()
        cur = conn.cursor()
        
        try:
            if esito == "vinto":
                # Paga il premio (raddoppio)
                cur.execute("UPDATE users SET wallet = wallet + %s WHERE user_id = %s", (self.somma, str(self.interaction.user.id)))
                txt = f"🏆 **Hai vinto!** Ti sono stati accreditati **{self.somma}$**."
                colore = discord.Color.green()
            elif esito == "pareggio":
                txt = "🤝 **Pareggio!** Non hai perso nulla."
                colore = discord.Color.light_gray()
            else:
                # Sottrae la scommessa
                cur.execute("UPDATE users SET wallet = wallet - %s WHERE user_id = %s", (self.somma, str(self.interaction.user.id)))
                txt = f"💀 **Hai perso {self.somma}$**. Il banco vince."
                colore = discord.Color.red()
            
            conn.commit()
        except Exception as e:
            print(f"Errore DB Blackjack: {e}")
        finally:
            cur.close()
            conn.close()

        emb = discord.Embed(title="🃏 Blackjack - Risultato Finale", color=colore)
        emb.add_field(name="Tu 👤", value=f"{self.mano_p} (Tot: {tot_p})", inline=True)
        emb.add_field(name="Banco 🏛️", value=f"{self.mano_b} (Tot: {tot_b})", inline=True)
        emb.add_field(name="Esito", value=txt, inline=False)
        
        await inter.response.edit_message(embed=emb, view=None)

# --- COMANDO SLASH ---
@bot.tree.command(name="blackjack", description="Gioca a Blackjack contro il banco")
async def blackjack(interaction: discord.Interaction, somma: int):
    # Recupero dati per controllo fondi
    u = get_user_data(interaction.user.id)
    
    if somma <= 0:
        return await interaction.response.send_message("❌ Inserisci una somma valida!", ephemeral=True)
    if u['wallet'] < somma:
        return await interaction.response.send_message(f"❌ Non hai abbastanza contanti! Hai solo {u['wallet']}$.", ephemeral=True)

    # Carte iniziali
    mano_p = [random.randint(2, 11), random.randint(2, 11)]
    mano_b = [random.randint(2, 11)]
    
    view = BlackjackView(interaction, somma, mano_p, mano_b)
    
    emb = discord.Embed(title="🃏 Blackjack", color=discord.Color.gold())
    emb.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
    emb.add_field(name="La tua mano 👤", value=f"{mano_p}\n**Totale: {view.get_tot(mano_p)}**", inline=True)
    emb.add_field(name="Banco 🏛️", value=f"[{mano_b[0]}, ?]\n**Totale: ?**", inline=True)
    emb.set_footer(text=f"Puntata: {somma}$")

    await interaction.response.send_message(embed=emb, view=view)



@bot.tree.command(name="roulette", description="Punta i tuoi soldi alla roulette (Attesa 10s)")
@app_commands.choices(puntata=[
    app_commands.Choice(name="🔴 Rosso (x2)", value="rosso"),
    app_commands.Choice(name="⚫ Nero (x2)", value="nero"),
    app_commands.Choice(name="🟢 Numero Singolo (x36)", value="numero")
])
async def roulette(interaction: discord.Interaction, puntata: str, somma: int, numero_scelto: int = None):
    u = get_user_data(interaction.user.id)
    if somma <= 0:
        return await interaction.response.send_message("❌ Inserisci una cifra valida!", ephemeral=True)
    if u['wallet'] < somma:
        return await interaction.response.send_message(f"❌ Non hai abbastanza contanti! (Hai {u['wallet']}$)", ephemeral=True)

    if puntata == "numero" and (numero_scelto is None or numero_scelto < 0 or numero_scelto > 36):
        return await interaction.response.send_message("❌ Se punti su un numero, scegline uno tra 0 e 36!", ephemeral=True)

    await interaction.response.send_message(f"🎰 **{interaction.user.display_name}** ha puntato **{somma}$** su **{puntata.upper()}**...\n*La pallina sta girando...* 🎡")
    
    await asyncio.sleep(10) # Attesa per creare suspense
    
    risultato = random.randint(0, 36)
    rossi = [1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36]
    colore_uscito = "rosso" if risultato in rossi else "nero" if risultato != 0 else "verde"
    emoji = "🔴" if colore_uscito == "rosso" else "⚫" if colore_uscito == "nero" else "🟢"

    vinto = False
    moltiplicatore = 2
    if puntata == "rosso" and colore_uscito == "rosso": vinto = True
    elif puntata == "nero" and colore_uscito == "nero": vinto = True
    elif puntata == "numero" and numero_scelto == risultato: 
        vinto = True
        moltiplicatore = 36

    conn = get_db_connection()
    cur = conn.cursor()
    
    if vinto:
        # Guadagno Netto: se punti 100 e vinci x2, ricevi +100 (totale 200)
        vincita_netta = somma * (moltiplicatore - 1)
        cur.execute("UPDATE users SET wallet = wallet + %s WHERE user_id = %s", (vincita_netta, str(interaction.user.id)))
        testo = f"✅ RISULTATO: **{risultato} {emoji}**. Hai vinto! Ti sono stati accreditati **{somma * moltiplicatore}$** 🎉"
    else:
        # Perdita: ti vengono sottratti i soldi puntati
        cur.execute("UPDATE users SET wallet = wallet - %s WHERE user_id = %s", (somma, str(interaction.user.id)))
        testo = f"💀 RISULTATO: **{risultato} {emoji}**. Hai perso **{somma}$**. La casa vince! 🏛️"
    
    conn.commit()
    cur.close(); conn.close()
    await interaction.channel.send(f"🎰 **{interaction.user.mention}**\n{testo}")


# ================= COMANDI STAFF =================

@bot.tree.command(name="staff_vedi_portafoglio", description="STAFF - Bilancio utente")
async def staff_vedi_portafoglio(interaction: Interaction, utente: discord.Member):
    if not is_staff(interaction): return await interaction.response.send_message("❌ No Staff.")
    u = get_user_data(utente.id)
    await interaction.response.send_message(f"💰 {utente.name}: Wallet {u['wallet']}$ | Bank {u['bank']}$")

@bot.tree.command(name="staff_vedi_inventario", description="STAFF - Inventario utente")
async def staff_vedi_inventario(interaction: Interaction, utente: discord.Member):
    if not is_staff(interaction): return await interaction.response.send_message("❌ No Staff.")
    conn = get_db_connection(); cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT item_name, quantity FROM inventory WHERE user_id = %s", (str(utente.id),))
    items = cur.fetchall()
    cur.close(); conn.close()
    desc = "\n".join([f"{i['item_name']} x{i['quantity']}" for i in items]) if items else "Vuoto."
    await interaction.response.send_message(f"🎒 Inventario {utente.name}:\n{desc}")

@bot.tree.command(name="staff_vedi_deposito", description="STAFF - Vedi un deposito fazione")
async def staff_vedi_deposito(interaction: Interaction):
    if not is_staff(interaction): return await interaction.response.send_message("❌ No Staff.")
    await interaction.response.defer()
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT role_id FROM depositi"); fazioni_id = [r[0] for r in cur.fetchall()]
    cur.close(); conn.close()
    
    async def mostra_staff(inter, rid):
        conn_i = get_db_connection(); cur_i = conn_i.cursor(cursor_factory=RealDictCursor)
        cur_i.execute("SELECT money FROM depositi WHERE role_id = %s", (rid,))
        m = cur_i.fetchone()['money']
        cur_i.execute("SELECT item_name, quantity FROM depositi_items WHERE role_id = %s", (rid,))
        it = cur_i.fetchall()
        r_obj = inter.guild.get_role(int(rid))
        emb = discord.Embed(title=f"🏦 Ispezione: {r_obj.name if r_obj else rid}", color=discord.Color.red())
        emb.add_field(name="Soldi", value=f"{m}$", inline=False)
        lista = "\n".join([f"📦 {i['item_name']} x{i['quantity']}" for i in it]) if it else "Vuoto"
        emb.add_field(name="Oggetti", value=lista, inline=False)
        await inter.followup.send(embed=emb); cur_i.close(); conn_i.close()

    view = discord.ui.View()
    options = [discord.SelectOption(label=interaction.guild.get_role(int(rid)).name if interaction.guild.get_role(int(rid)) else rid, value=rid) for rid in fazioni_id]
    sel = discord.ui.Select(options=options[:25])
    async def call(i): 
        for it in view.children: it.disabled = True
        await i.response.edit_message(view=view); await mostra_staff(i, sel.values[0])
    sel.callback = call; view.add_item(sel)
    await interaction.followup.send("Quale deposito ispezioni?", view=view, ephemeral=True)

# ================= COMANDI ADMIN =================

@bot.tree.command(name="aggiungisoldi", description="ADMIN - Regala soldi")
async def aggiungisoldi(interaction: Interaction, utente: discord.Member, importo: int):
    if not interaction.user.guild_permissions.administrator: return
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("UPDATE users SET wallet = wallet + %s WHERE user_id = %s", (importo, str(utente.id)))
    conn.commit(); cur.close(); conn.close()
    await interaction.response.send_message(f"✅ Admin ha aggiunto **{importo}$** a {utente.mention}")

@bot.tree.command(name="rimuovisoldi", description="ADMIN - Togli soldi")
async def rimuovisoldi(interaction: Interaction, utente: discord.Member, importo: int):
    if not interaction.user.guild_permissions.administrator: return
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("UPDATE users SET wallet = GREATEST(0, wallet - %s) WHERE user_id = %s", (importo, str(utente.id)))
    conn.commit(); cur.close(); conn.close()
    await interaction.response.send_message(f"✅ Admin ha rimosso **{importo}$** a {utente.mention}")

@bot.tree.command(name="aggiungi_item", description="ADMIN - Regala item")
async def aggiungi_item(interaction: Interaction, utente: discord.Member, nome: str, quantita: int = 1):
    if not interaction.user.guild_permissions.administrator: return
    await interaction.response.defer()
    nome_e = await cerca_item_smart(interaction, nome, "items")
    if not nome_e: return
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("INSERT INTO inventory (user_id, item_name, quantity) VALUES (%s, %s, %s) ON CONFLICT (user_id, item_name) DO UPDATE SET quantity = inventory.quantity + %s", (str(utente.id), nome_e, quantita, quantita))
    conn.commit(); cur.close(); conn.close()
    await interaction.followup.send(f"✅ Admin ha dato {quantita}x **{nome_e}** a {utente.mention}")

@bot.tree.command(name="rimuovi_item", description="ADMIN - Togli item")
async def rimuovi_item(interaction: Interaction, utente: discord.Member, nome: str, quantita: int = 1):
    if not interaction.user.guild_permissions.administrator: return
    await interaction.response.defer()
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("UPDATE inventory SET quantity = GREATEST(0, quantity - %s) WHERE user_id = %s AND item_name ILIKE %s", (quantita, str(utente.id), f"%{nome}%"))
    cur.execute("DELETE FROM inventory WHERE quantity <= 0")
    conn.commit(); cur.close(); conn.close()
    await interaction.followup.send(f"✅ Admin ha rimosso {quantita}x **{nome}** a {utente.mention}")

@bot.tree.command(name="crea_item_shop", description="ADMIN - Crea item shop")
async def crea_item_shop(interaction: Interaction, nome: str, descrizione: str, prezzo: int, ruolo: discord.Role = None):
    if not interaction.user.guild_permissions.administrator: return
    rid = str(ruolo.id) if ruolo else "None"
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("INSERT INTO items (name, description, price, role_required) VALUES (%s,%s,%s,%s) ON CONFLICT (name) DO UPDATE SET price=EXCLUDED.price, description=EXCLUDED.description, role_required=EXCLUDED.role_required", (nome, descrizione, prezzo, rid))
    conn.commit(); cur.close(); conn.close()
    await interaction.response.send_message(f"✅ Item **{nome}** creato/aggiornato nello shop.")

@bot.tree.command(name="elimina_item_shop", description="ADMIN - Elimina definitivamente item dallo shop")
async def elimina_item_shop(interaction: Interaction, nome: str):
    if not interaction.user.guild_permissions.administrator: return
    await interaction.response.defer()
    nome_e = await cerca_item_smart(interaction, nome, "items")
    if not nome_e: return
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("DELETE FROM items WHERE name = %s", (nome_e,))
    conn.commit(); cur.close(); conn.close()
    await interaction.followup.send(f"🗑️ L'item **{nome_e}** è stato rimosso dallo shop.")

@bot.tree.command(name="registra_fazione", description="ADMIN - Registra ruolo fazione")
async def registra_fazione(interaction: Interaction, ruolo: discord.Role):
    if not interaction.user.guild_permissions.administrator: return
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("INSERT INTO depositi (role_id, money) VALUES (%s, 0) ON CONFLICT DO NOTHING", (str(ruolo.id),))
    conn.commit(); cur.close(); conn.close()
    await interaction.response.send_message(f"✅ Fazione **{ruolo.name}** registrata nel sistema.")

@bot.tree.command(name="wipe_utente", description="ADMIN - Reset totale utente")
async def wipe_utente(interaction: Interaction, utente: discord.Member):
    if not interaction.user.guild_permissions.administrator: return
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("UPDATE users SET wallet = 500, bank = 0 WHERE user_id = %s", (str(utente.id),))
    cur.execute("DELETE FROM inventory WHERE user_id = %s", (str(utente.id),))
    conn.commit(); cur.close(); conn.close()
    await interaction.response.send_message(f"🧹 Reset totale per **{utente.name}**.")

# ================= WEB SERVER & START =================

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"✅ {bot.user} Online! Tutti i comandi sincronizzati e pubblici.")

app = Flask("")
@app.route("/")
def home(): return "Bot Online"
def run(): app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
threading.Thread(target=run).start()

bot.run(TOKEN)
