import discord
from discord import app_commands
from discord.ext import commands
import os
import psycopg2
import asyncio
from flask import Flask
from threading import Thread

# Inizializzazione Flask
app = Flask('')

@app.route('/')
def home():
    return "Bot is alive!"

def run():
    # Il server ascolta sulla porta 8080 (standard per Replit/Render)
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    """Avvia il server in un thread separato per non bloccare il bot"""
    t = Thread(target=run)
    t.start()

# --- CONFIGURAZIONE VARIABILI D'AMBIENTE ---
TOKEN = os.getenv("TOKEN")
DB_URL = os.getenv("DATABASE_URL")

# --- FUNZIONE CONNESSIONE DB ---
def get_db_connection():
    return psycopg2.connect(DB_URL)

class MyBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.all()
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        # 1. Registra la view persistente (usa i custom_id per ricollegarsi)
        self.add_view(InterazioneCucinaRealistica())
        
        # 2. Sincronizza i comandi slash con Discord
        await self.tree.sync()
        
        print(f"✅ Sistema pronto: Comandi sincronizzati e View persistenti caricate per {self.user}")

# Istanza del bot
bot = MyBot()

# --- HELPER: LOGICA CONSUMO ---
async def consuma_item(interaction: discord.Interaction, item_nome: str, tipo_richiesto: str):
    conn = get_db_connection()
    cur = conn.cursor()
    
    # Controllo se l'utente esiste nel DB (per evitare errori di stats)
    cur.execute("INSERT INTO users (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING", (str(interaction.user.id),))
    
    # Verifica possesso e tipo
    cur.execute("""
        SELECT i.quantity, it.tipo, it.valore_ripristino 
        FROM inventory i JOIN items it ON i.item_name = it.name 
        WHERE i.user_id = %s AND i.item_name = %s AND it.tipo = %s
    """, (str(interaction.user.id), item_nome, tipo_richiesto))
    res = cur.fetchone()

    if not res:
        cur.close(); conn.close()
        return await interaction.response.send_message(f"❌ Non hai **{item_nome}** nel tuo inventario o non è l'azione corretta!", ephemeral=True)

    quantita, tipo, valore = res
    
    # Logica Stats
    if tipo == "cibo":
        query_stats = "UPDATE users SET fame = LEAST(fame + %s, 100) WHERE user_id = %s"
        azione = "mangiato"
    elif tipo == "bevanda":
        query_stats = "UPDATE users SET sete = LEAST(sete + %s, 100) WHERE user_id = %s"
        azione = "bevuto"
    elif tipo == "fumo":
        query_stats = "UPDATE users SET stress = GREATEST(stress - %s, 0) WHERE user_id = %s"
        azione = "fumato"

    cur.execute(query_stats, (valore, str(interaction.user.id)))
    
    # Rimozione Inventario
    if quantita > 1:
        cur.execute("UPDATE inventory SET quantity = quantity - 1 WHERE user_id = %s AND item_name = %s", (str(interaction.user.id), item_nome))
    else:
        cur.execute("DELETE FROM inventory WHERE user_id = %s AND item_name = %s", (str(interaction.user.id), item_nome))
    
    conn.commit()
    cur.close(); conn.close()
    await interaction.response.send_message(f"✅ Hai {azione} **{item_nome}**!")

# --- AUTOCOMPLETE ---
async def get_filtered_items(interaction: discord.Interaction, current: str, tipo: str):
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("""
        SELECT i.item_name FROM inventory i 
        JOIN items it ON i.item_name = it.name 
        WHERE i.user_id = %s AND it.tipo = %s AND i.item_name ILIKE %s
    """, (str(interaction.user.id), tipo, f"%{current}%"))
    items = cur.fetchall()
    cur.close(); conn.close()
    return [app_commands.Choice(name=item[0], value=item[0]) for item in items][:25]

# --- COMANDI UTENTE ---

@bot.tree.command(name="mangia", description="Mangia un alimento")
@app_commands.autocomplete(item=lambda inter, curr: get_filtered_items(inter, curr, 'cibo'))
async def mangia(interaction: discord.Interaction, item: str):
    await consuma_item(interaction, item, "cibo")

@bot.tree.command(name="bevi", description="Bevi una bevanda")
@app_commands.autocomplete(item=lambda inter, curr: get_filtered_items(inter, curr, 'bevanda'))
async def bevi(interaction: discord.Interaction, item: str):
    await consuma_item(interaction, item, "bevanda")

@bot.tree.command(name="fuma", description="Fuma per rilassarti")
@app_commands.autocomplete(item=lambda inter, curr: get_filtered_items(inter, curr, 'fumo'))
async def fuma(interaction: discord.Interaction, item: str):
    await consuma_item(interaction, item, "fumo")

@bot.tree.command(name="status", description="Vedi Fame, Sete e Stress")
async def status(interaction: discord.Interaction):
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT fame, sete, stress FROM users WHERE user_id = %s", (str(interaction.user.id),))
    res = cur.fetchone()
    cur.close(); conn.close()
    
    if not res: return await interaction.response.send_message("❌ Status non disponibile. Inizia a consumare qualcosa!", ephemeral=True)
    
    fame, sete, stress = res
    def make_bar(n): return "🟩" * (n // 10) + "⬜" * (10 - (n // 10))

    embed = discord.Embed(title=f"📊 Status: {interaction.user.name}", color=discord.Color.blue())
    embed.add_field(name=f"🍔 Fame: {fame}%", value=make_bar(fame), inline=False)
    embed.add_field(name=f"🥤 Sete: {sete}%", value=make_bar(sete), inline=False)
    embed.add_field(name=f"🚬 Stress: {stress}%", value=make_bar(100-stress), inline=False) # Invertito per coerenza visiva
    await interaction.response.send_message(embed=embed)

# --- COMANDO STAFF ---

@bot.tree.command(name="crea_item_shop", description="STAFF - Aggiungi item allo shop")
@app_commands.choices(tipo=[
    app_commands.Choice(name="Cibo", value="cibo"),
    app_commands.Choice(name="Bevanda", value="bevanda"),
    app_commands.Choice(name="Fumo", value="fumo"),
    app_commands.Choice(name="Normale", value="normale")
])
async def crea_item_shop(interaction: discord.Interaction, nome: str, descrizione: str, prezzo: int, tipo: str, valore: int = 20):
    # Inserire qui il controllo ruoli staff se necessario
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("""
        INSERT INTO items (name, description, price, tipo, valore_ripristino) 
        VALUES (%s, %s, %s, %s, %s) 
        ON CONFLICT (name) DO UPDATE SET tipo=EXCLUDED.tipo, valore_ripristino=EXCLUDED.valore_ripristino, price=EXCLUDED.price
    """, (nome, descrizione, prezzo, tipo, valore))
    conn.commit(); cur.close(); conn.close()
    await interaction.response.send_message(f"✅ Item **{nome}** ({tipo}) registrato con valore {valore}.")
# --- VIEW PERSISTENTE ---
class InterazioneCucinaRealistica(discord.ui.View):
    def __init__(self, piatto=None, info=None, user_id=None):
        # Timeout=None rende la view persistente
        super().__init__(timeout=None)
        self.piatto = piatto
        self.info = info
        self.user_id = user_id
        self.strumento_attivo = None
        self.stato_cottura = 0
        self.cliccato_cottura = False

    def crea_embed(self, messaggio):
        color = discord.Color.green() if "✅" in messaggio else discord.Color.blue()
        embed = discord.Embed(title=f"👨‍🍳 Cucina: {self.piatto}", description=messaggio, color=color)
        embed.add_field(name="🛠️ Strumento", value=f"`{self.strumento_attivo or 'Mani Vuote'}`")
        
        fasi = ["🔪 Prep", "🔥 Cottura", "🍽️ Fine"]
        bar = " ".join([f"**{f}** {'✅' if self.stato_cottura > i else '⚪'}" for i, f in enumerate(fasi)])
        embed.add_field(name="Avanzamento", value=bar, inline=False)
        return embed

    @discord.ui.select(
        placeholder="Scegli lo strumento...",
        custom_id="cucina:select", # ID univoco per persistenza
        options=[
            discord.SelectOption(label="Tagliere", emoji="🔪"),
            discord.SelectOption(label="Pentola", emoji="🍲"),
            discord.SelectOption(label="Forno a Legna", emoji="🔥"),
            discord.SelectOption(label="Frusta/Sbattitore", emoji="🥣"),
            discord.SelectOption(label="Padella", emoji="🍳")
        ]
    )
    async def select_strumento(self, interaction: discord.Interaction, select: discord.ui.Select):
        if self.user_id and interaction.user.id != self.user_id:
            return await interaction.response.send_message("❌ Non è la tua postazione!", ephemeral=True)
        
        self.strumento_attivo = select.values[0]
        await interaction.response.edit_message(embed=self.crea_embed(f"Hai preso: **{self.strumento_attivo}**."))

    @discord.ui.button(label="1. Prepara", style=discord.ButtonStyle.secondary, custom_id="cucina:prepara")
    async def prepara(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.strumento_attivo != "Tagliere":
            return await interaction.response.send_message("❌ Usa il Tagliere!", ephemeral=True)
        
        self.stato_cottura = 1
        await interaction.response.edit_message(embed=self.crea_embed("Base pronta! Ora usa lo strumento finale."), view=self)

    @discord.ui.button(label="2. Completa", style=discord.ButtonStyle.danger, custom_id="cucina:cuoci")
    async def cuoci(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Logica di controllo strumento necessario (semplificata per brevità)
        if self.stato_cottura < 1:
            return await interaction.response.send_message("❌ Prepara prima gli ingredienti!", ephemeral=True)
        
        self.cliccato_cottura = True
        self.stato_cottura = 2
        await interaction.response.edit_message(embed=self.crea_embed("⏳ In lavorazione..."), view=None)
        
        # Simulazione cottura e salvataggio
        await asyncio.sleep(5) 
        
        conn = psycopg2.connect(os.getenv("DATABASE_URL"))
        cur = conn.cursor()
        cur.execute("INSERT INTO inventory (user_id, item_name, quantity) VALUES (%s, %s, 1) ON CONFLICT (user_id, item_name) DO UPDATE SET quantity = inventory.quantity + 1", (str(interaction.user.id), self.piatto))
        conn.commit(); cur.close(); conn.close()
        
        await interaction.followup.send(f"✅ {interaction.user.mention}, il tuo **{self.piatto}** è pronto!", ephemeral=False)

# --- COMANDO ---
@bot.tree.command(name="cucina", description="Inizia a cucinare")
async def cucina(interaction: discord.Interaction, piatto: str):
    if piatto not in MENU_DATI:
        return await interaction.response.send_message("❌ Piatto non valido.", ephemeral=True)
    
    # Crea la view specifica per questa sessione
    view = InterazioneCucinaRealistica(piatto=piatto, info=MENU_DATI[piatto], user_id=interaction.user.id)
    await interaction.response.send_message(embed=view.crea_embed("Chef, ai fornelli!"), view=view)

if __name__ == "__main__":
    keep_alive()  # <--- Avvia il server web
    bot.run(TOKEN) # <--- Avvia il bot Discord

