import os
import discord
import psycopg2
import datetime
from discord import app_commands
from discord.ext import commands
import os
from flask import Flask
import threading
import os

app = Flask(__name__)

@app.route('/')
def index():
    return "Bot is alive!"

def run():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

# Avvia Flask in un thread separato così non blocca il resto del bot
threading.Thread(target=run).start()


# --- CONFIGURAZIONE AMBIENTE (RENDER/LOCAL) ---
TOKEN = os.getenv("DISCORD_TOKEN")
DB_URI = os.getenv("DB_URL") # Stringa di connessione PostgreSQL di Supabase

# --- HELPER DATABASE ---
def execute_query(query, params=None, fetch=False):
    with psycopg2.connect(DB_URI) as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            if fetch:
                return cur.fetchone() if "SELECT" in query.upper() else None
            conn.commit()

def execute_query_all(query, params=None):
    with psycopg2.connect(DB_URI) as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            return cur.fetchall()

def inizializza_db():
    queries = [
        # ... tabelle precedenti ...
        """
        CREATE TABLE IF NOT EXISTS fatture (
            id SERIAL PRIMARY KEY,
            emittente_id TEXT,
            destinatario_id TEXT,
            importo INTEGER,
            causale TEXT,
            stato TEXT DEFAULT 'Pendente'
        );
        """
    ]
    for q in queries:
        execute_query(q)

# --- CLASSE CORE DEL BOT ---
class VinewoodBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.all()
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        inizializza_db()
        await self.tree.sync()
        print(f"✅ Bot Online: {self.user} | Comandi Sincronizzati")

bot = VinewoodBot()

# ==========================================
# 1. CATEGORIA: SETUP & RUOLI
# ==========================================
class RoleDropdown(discord.ui.Select):
    def __init__(self, placeholder, db_column, roles):
        options = [discord.SelectOption(label=r.name, value=str(r.id)) for r in roles[:25]]
        super().__init__(placeholder=placeholder, options=options, custom_id=db_column)

    async def callback(self, itx: discord.Interaction):
        await itx.response.defer(ephemeral=True)
        query = f"""
            INSERT INTO config_ruoli (guild_id, {self.custom_id}) VALUES (%s, %s)
            ON CONFLICT (guild_id) DO UPDATE SET {self.custom_id} = EXCLUDED.{self.custom_id};
        """
        execute_query(query, (str(itx.guild.id), self.values[0]))
        await itx.followup.send(f"✅ Ruolo per `{self.custom_id}` salvato!", ephemeral=True)

class SetupView(discord.ui.View):
    def __init__(self, roles):
        super().__init__(timeout=None)
        self.add_item(RoleDropdown("Ruolo Polizia", "polizia_id", roles))
        self.add_item(RoleDropdown("Ruolo Meccanico", "meccanico_id", roles))
        self.add_item(RoleDropdown("Ruolo Medico", "medico_id", roles))
        self.add_item(RoleDropdown("Ruolo Staff", "staff_id", roles))

@bot.tree.command(name="setup-ruoli", description="Configura i ruoli lavorativi del server")
@app_commands.checks.has_permissions(administrator=True)
async def setup_ruoli(itx: discord.Interaction):
    roles = [r for r in itx.guild.roles if not r.managed and not r.is_default()]
    await itx.response.send_message("⚙️ **Configurazione Ruoli RP**", view=SetupView(roles), ephemeral=True)

# ==========================================
# 2. CATEGORIA: VEICOLI & CARBURANTE
# ==========================================
@bot.tree.command(name="accendo-motore", description="Avvia il motore (inizia il consumo)")
async def accendo(itx: discord.Interaction, targa: str):
    await itx.response.defer()
    targa = targa.upper()
    res = execute_query("SELECT carburante FROM veicoli WHERE targa = %s", (targa,), fetch=True)
    
    if not res: return await itx.followup.send("❌ Veicolo non registrato.")
    if res[0] <= 0: return await itx.followup.send("🪫 Serbatoio vuoto. Il motore non parte!")

    execute_query("UPDATE veicoli SET ultimo_avvio = %s WHERE targa = %s", (datetime.datetime.now(), targa))
    await itx.followup.send(f"🔑 Motore avviato per **{targa}**. Benzina: **{res[0]:.1f}%**")

@bot.tree.command(name="spengo-motore", description="Spegne il motore e salva il consumo")
async def spengo(itx: discord.Interaction, targa: str):
    await itx.response.defer()
    targa = targa.upper()
    res = execute_query("SELECT ultimo_avvio, carburante FROM veicoli WHERE targa = %s", (targa,), fetch=True)
    
    if not res or res[0] is None: return await itx.followup.send("❌ Il motore non era acceso.")

    durata = (datetime.datetime.now() - res[0]).total_seconds() / 60
    consumo = durata * 0.4 # 0.4% al minuto
    nuovo_lv = max(0, res[1] - consumo)

    execute_query("UPDATE veicoli SET carburante = %s, ultimo_avvio = NULL WHERE targa = %s", (nuovo_lv, targa))
    await itx.followup.send(f"🔌 Motore spento. Consumato: **{consumo:.2f}%**. Residuo: **{nuovo_lv:.1f}%**")

@bot.tree.command(name="rifornimento", description="Fai benzina ($1.50 per 1%)")
async def benzina(itx: discord.Interaction, targa: str, percentuale: float):
    await itx.response.defer()
    costo = percentuale * 1.50
    user_id = str(itx.user.id)

    soldi = execute_query("SELECT contanti FROM utenti WHERE discord_id = %s", (user_id,), fetch=True)
    if not soldi or soldi[0] < costo:
        return await itx.followup.send(f"❌ Ti servono **${costo:.2f}** in contanti.")

    res_v = execute_query("SELECT carburante FROM veicoli WHERE targa = %s", (targa.upper(),), fetch=True)
    if not res_v: return await itx.followup.send("❌ Veicolo inesistente.")

    nuovo_c = min(100.0, res_v[0] + percentuale)
    execute_query("UPDATE utenti SET contanti = contanti - %s WHERE discord_id = %s", (costo, user_id))
    execute_query("UPDATE veicoli SET carburante = %s WHERE targa = %s", (nuovo_c, targa.upper()))
    
    await itx.followup.send(f"⛽ Rifornimento: +{percentuale}% | Pagato: **${costo:.2f}**")

# ==========================================
# 3. CATEGORIA: ECONOMIA & INVENTARIO
# ==========================================
@bot.tree.command(name="portafoglio", description="Mostra i tuoi averi")
async def portafoglio(itx: discord.Interaction):
    await itx.response.defer(ephemeral=True)
    res = execute_query("SELECT contanti, banca FROM utenti WHERE discord_id = %s", (str(itx.user.id),), fetch=True)
    if res:
        await itx.followup.send(f"💵 **Contanti:** ${res[0]}\n💳 **Banca:** ${res[1]}")
    else:
        await itx.followup.send("❌ Profilo non trovato. Usa `/inizia-rp`.")

@bot.tree.command(name="perquisizione", description="[POLIZIA] Controlla un cittadino")
async def perquisisci(itx: discord.Interaction, cittadino: discord.Member):
    await itx.response.defer()
    # Controllo ruolo polizia
    p_role = execute_query("SELECT polizia_id FROM config_ruoli WHERE guild_id = %s", (str(itx.guild.id),), fetch=True)
    if not p_role or not any(r.id == int(p_role[0]) for r in itx.user.roles):
        return await itx.followup.send("❌ Non sei autorizzato (Polizia).")

    soldi = execute_query("SELECT contanti FROM utenti WHERE discord_id = %s", (str(cittadino.id),), fetch=True)
    items = execute_query_all("SELECT item_name, quantita FROM inventari WHERE user_id = %s", (str(cittadino.id),))
    
    lista = "\n".join([f"- {i[0]} x{i[1]}" for i in items]) if items else "Nessun oggetto."
    await itx.followup.send(f"🔍 **Perquisizione: {cittadino.display_name}**\n💵 Contanti: **${soldi[0] if soldi else 0}**\n🎒 Oggetti:\n{lista}")

# ==========================================
# 4. CATEGORIA: STAFF & UTILITY
# ==========================================
@bot.tree.command(name="me", description="Azione Roleplay")
async def me(itx: discord.Interaction, azione: str):
    await itx.response.send_message(f"*** {itx.user.display_name} {azione} ***")

@bot.tree.command(name="give-money", description="[STAFF] Regala soldi")
async def give_money(itx: discord.Interaction, utente: discord.Member, ammontare: int):
    await itx.response.defer()
    execute_query("UPDATE utenti SET banca = banca + %s WHERE discord_id = %s", (ammontare, str(utente.id)))
    await itx.followup.send(f"✅ Accreditati **${ammontare}** a {utente.mention}.")

# ==========================================
# INIZIALIZZAZIONE DB (In fondo al file)
# ==========================================
def inizializza_db():
    queries = [
        """CREATE TABLE IF NOT EXISTS config_ruoli (guild_id TEXT PRIMARY KEY, polizia_id TEXT, meccanico_id TEXT, medico_id TEXT, staff_id TEXT);""",
        """CREATE TABLE IF NOT EXISTS utenti (discord_id TEXT PRIMARY KEY, contanti INTEGER DEFAULT 500, banca INTEGER DEFAULT 2000, lavoro TEXT DEFAULT 'Civile');""",
        """CREATE TABLE IF NOT EXISTS veicoli (targa TEXT PRIMARY KEY, proprietario_id TEXT, modello TEXT, integrita INTEGER DEFAULT 100, carburante FLOAT DEFAULT 100.0, ultimo_avvio TIMESTAMP);""",
        """CREATE TABLE IF NOT EXISTS inventari (id SERIAL PRIMARY KEY, user_id TEXT, item_name TEXT, quantita INTEGER DEFAULT 1);"""
    ]
    for q in queries:
        execute_query(q)

@bot.tree.command(name="aiuto-rp", description="Mostra tutti i comandi disponibili su Platinum RP")
async def aiuto(itx: discord.Interaction):
    embed = discord.Embed(title="💎 Platinum Roleplay - Guida Comandi", color=discord.Color.from_rgb(229, 228, 226))
    
    embed.add_field(name="🚗 Veicoli", value="`/accendo-motore`, `/spengo-motore`, `/rifornimento`, `/ispeziona-veicolo`", inline=False)
    embed.add_field(name="💰 Economia", value="`/portafoglio`, `/bancomat`, `/fattura`, `/compra-casa`", inline=False)
    embed.add_field(name="👮 Legge", value="`/arresto`, `/perquisizione`, `/cerca-persona`, `/mostra-distintivo`, `/annuncio`", inline=False)
    embed.add_field(name="🎭 Roleplay", value="`/me`, `/documenti`, `/morte`, `/cura`, `/bacio`, `/anonimo`", inline=False)
    embed.add_field(name="⚙️ Staff", value="`/setup-ruoli`, `/give-money`, `/set-lavoro`, `/reset-inv`", inline=False)
    
    await itx.response.send_message(embed=embed, ephemeral=True)
@bot.tree.command(name="borseggio", description="Tenta di rubare dalla tasca di un cittadino")
@app_commands.checks.cooldown(1, 3600) # Una volta all'ora
async def borseggio(itx: discord.Interaction, vittima: discord.Member):
    await itx.response.defer()
    import random

    if vittima.id == itx.user.id:
        return await itx.followup.send("❓ Stai provando a rubare a te stesso?")

    # 20% di successo
    if random.randint(1, 100) <= 20:
        res_vittima = execute_query("SELECT contanti FROM utenti WHERE discord_id = %s", (str(vittima.id),), fetch=True)
        if not res_vittima or res_vittima[0] < 50:
            return await itx.followup.send(f"💸 Hai frugato nelle tasche di {vittima.mention} ma è al verde!")

        bottino = random.randint(10, int(res_vittima[0] * 0.3)) # Ruba fino al 30% dei contanti
        execute_query("UPDATE utenti SET contanti = contanti - %s WHERE discord_id = %s", (bottino, str(vittima.id)))
        execute_query("UPDATE utenti SET contanti = contanti + %s WHERE discord_id = %s", (bottino, str(itx.user.id)))
        
        await itx.followup.send(f"🥷 Sei un ombra! Hai sfilato **${bottino}** a {vittima.mention} senza farti notare.")
    else:
        await itx.followup.send(f"🚨 Ti sei fatto scoprire! {vittima.mention} ha sentito la tua mano in tasca!")
@bot.tree.command(name="annuncio", description="[STAFF/POLIZIA] Invia un annuncio globale Platinum RP")
async def annuncio(itx: discord.Interaction, titolo: str, messaggio: str):
    await itx.response.defer()
    
    # Controllo se l'utente ha il ruolo Staff o Polizia
    res = execute_query("SELECT staff_id, polizia_id FROM config_ruoli WHERE guild_id = %s", (str(itx.guild.id),), fetch=True)
    is_auth = any(r.id in [int(res[0]), int(res[1])] for r in itx.user.roles if res)

    if not is_auth:
        return await itx.followup.send("❌ Non hai i permessi per inviare annunci globali.")

    embed = discord.Embed(title=f"📢 {titolo.upper()}", description=messaggio, color=discord.Color.red())
    embed.set_author(name="Platinum Roleplay - Comunicazione Ufficiale")
    embed.set_footer(text=f"Inviato da: {itx.user.display_name}")
    
    await itx.channel.send(content="@everyone", embed=embed)
    await itx.followup.send("✅ Annuncio inviato.", ephemeral=True)

@bot.tree.command(name="cerca-persona", description="[POLIZIA] Cerca un cittadino nel database Platinum")
async def cerca_persona(itx: discord.Interaction, cittadino: discord.Member):
    await itx.response.defer()
    
    # Query per vedere fedina penale (tabella arresti)
    arresti = execute_query_all("SELECT motivo FROM arresti WHERE detenuto_id = %s", (str(cittadino.id),))
    
    embed = discord.Embed(title=f"📑 Archivio Centrale Platinum: {cittadino.display_name}", color=discord.Color.blue())
    if arresti:
        lista_crimini = "\n".join([f"- {a[0]}" for a in arresti])
        embed.add_field(name="Precedenti Penali", value=lista_crimini, inline=False)
    else:
        embed.add_field(name="Fedina Penale", value="Limpida - Nessun precedente trovato.", inline=False)
    
    await itx.followup.send(embed=embed)

@bot.tree.command(name="morte", description="Segnala che sei finito a terra incosciente")
async def morte(itx: discord.Interaction):
    # Invia un log nel canale dove viene usato, ma potresti anche mandarlo in un canale 'dispatch'
    await itx.response.send_message(f"🚑 **[DISPATCH EMS]**: Un cittadino ({itx.user.mention}) è a terra incosciente a {itx.channel.name}! Richiesto intervento immediato.")

@bot.tree.command(name="cura", description="[MEDICO] Rianima un cittadino ferito")
async def cura(itx: discord.Interaction, cittadino: discord.Member):
    await itx.response.defer()
    
    # Controllo ruolo Medico dal DB
    res = execute_query("SELECT medico_id FROM config_ruoli WHERE guild_id = %s", (str(itx.guild.id),), fetch=True)
    if not res or not any(r.id == int(res[0]) for r in itx.user.roles):
        return await itx.followup.send("❌ Solo il personale medico può utilizzare questo comando.")

    await itx.followup.send(f"💉 {itx.user.mention} ha prestato le prime cure a {cittadino.mention}. Il cittadino è ora fuori pericolo!")
@bot.tree.command(name="modifica-libretto", description="[MECCANICO] Cambia il proprietario registrato di un veicolo")
async def mod_libretto(itx: discord.Interaction, targa: str, nuovo_proprietario: discord.Member):
    await itx.response.defer()
    
    res_m = execute_query("SELECT meccanico_id FROM config_ruoli WHERE guild_id = %s", (str(itx.guild.id),), fetch=True)
    if not res_m or not any(r.id == int(res_m[0]) for r in itx.user.roles):
        return await itx.followup.send("❌ Permesso negato. Devi essere un Meccanico.")

    execute_query("UPDATE veicoli SET proprietario_id = %s WHERE targa = %s", (str(nuovo_proprietario.id), targa.upper()))
    await itx.followup.send(f"📑 Il libretto del veicolo **{targa.upper()}** è stato aggiornato. Nuovo proprietario: {nuovo_proprietario.mention}")
@bot.tree.command(name="fattura", description="Emetti una fattura a un cittadino")
async def fattura(itx: discord.Interaction, utente: discord.Member, importo: int, causale: str):
    await itx.response.defer()
    
    # Salviamo la fattura nel DB (aggiungi la tabella 'fatture' se vuoi renderle pagabili via comando)
    # Per ora facciamo un'emissione testuale ufficiale
    embed = discord.Embed(title="📄 FATTURA ELETTRONICA", color=discord.Color.gold())
    embed.add_field(name="Emittente", value=itx.user.display_name, inline=True)
    embed.add_field(name="Destinatario", value=utente.display_name, inline=True)
    embed.add_field(name="Importo", value=f"${importo}", inline=False)
    embed.add_field(name="Causale", value=causale, inline=False)
    embed.set_footer(text="Pagabile presso la banca o via comando /paga-fattura")
    
    await itx.followup.send(content=f"{utente.mention}, hai ricevuto una nuova fattura!", embed=embed)
@bot.tree.command(name="documenti", description="Mostra i tuoi documenti a un altro cittadino")
async def documenti(itx: discord.Interaction, utente: discord.Member):
    await itx.response.defer()
    
    res = execute_query("SELECT lavoro FROM utenti WHERE discord_id = %s", (str(itx.user.id),), fetch=True)
    lavoro = res[0] if res else "Civile"
    
    embed = discord.Embed(title="🪪 DOCUMENTO D'IDENTITÀ", color=discord.Color.dark_blue())
    embed.add_field(name="Nome", value=itx.user.display_name, inline=True)
    embed.add_field(name="Professione", value=lavoro, inline=True)
    embed.set_thumbnail(url=itx.user.display_avatar.url)
    
    await itx.followup.send(f"{utente.mention}, {itx.user.mention} ti ha mostrato i documenti.", embed=embed)

@bot.tree.command(name="bacio", description="Manda un bacio a qualcuno")
async def bacio(itx: discord.Interaction, utente: discord.Member):
    await itx.response.send_message(f"💋 {itx.user.mention} ha dato un bacio a {utente.mention}!")

@bot.tree.command(name="anonimo", description="Invia un messaggio nel Deep Web")
async def anonimo(itx: discord.Interaction, messaggio: str):
    # Messaggio che non mostra chi lo ha inviato nel canale pubblico
    await itx.response.send_message("Messaggio inviato nell'ombra...", ephemeral=True)
    await itx.channel.send(f"👤 **[ANONIMO]**: {messaggio}")

if __name__ == "__main__":
    # Avvia il server Flask in un thread separato

    bot.run(TOKEN)

