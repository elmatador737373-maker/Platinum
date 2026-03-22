import os
import discord
import psycopg2
import datetime
from discord import app_commands
from discord.ext import commands

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

if __name__ == "__main__":
    bot.run(TOKEN)

