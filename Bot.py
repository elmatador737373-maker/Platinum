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
