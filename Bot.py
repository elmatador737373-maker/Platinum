import discord
from discord import app_commands, ui
from discord.ext import commands
import psycopg2
import os
import random
import string

TOKEN = os.getenv("DISCORD_TOKEN")
DB_URL = os.getenv("DATABASE_URL")

def db_execute(query, params=None, fetch=False):
    with psycopg2.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            if fetch: return cur.fetchall()
            conn.commit()

class RPBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=discord.Intents.all())
    async def setup_hook(self):
        await self.tree.sync()

bot = RPBot()

# --- ⚙️ SETUP & CONFIG ---
@bot.tree.command(name="setup_server")
@app_commands.checks.has_permissions(administrator=True)
async def setup(it: discord.Interaction, cittadino: discord.Role, polizia: discord.Role, meccanico: discord.Role, staff: discord.Role):
    db_execute("INSERT INTO config_server VALUES (%s,%s,%s,%s,%s) ON CONFLICT (guild_id) DO UPDATE SET ruolo_polizia=EXCLUDED.ruolo_polizia, ruolo_cittadino=EXCLUDED.ruolo_cittadino, ruolo_meccanico=EXCLUDED.ruolo_meccanico, ruolo_staff=EXCLUDED.ruolo_staff", (it.guild.id, polizia.id, cittadino.id, meccanico.id, staff.id))
    await it.response.send_message("✅ Server Configurato!")

# --- 📱 TELEFONO & BANCA ---
class BankModal(ui.Modal, title="Operazione Bancaria"):
    amount = ui.TextInput(label="Cifra")
    def __init__(self, mode):
        super().__init__()
        self.mode = mode
    async def on_submit(self, it: discord.Interaction):
        val = int(self.amount.value)
        if self.mode == "dep":
            db_execute("UPDATE utenti SET contanti=contanti-%s, banca=banca+%s WHERE user_id=%s", (val, val, it.user.id))
        else:
            db_execute("UPDATE utenti SET banca=banca-%s, contanti=contanti+%s WHERE user_id=%s", (val, val, it.user.id))
        await it.response.send_message(f"✅ Operazione di {val}€ completata!", ephemeral=True)

class PhoneView(ui.View):
    @ui.button(label="Deposita", style=discord.ButtonStyle.green)
    async def dep(self, it, b): await it.response.send_modal(BankModal("dep"))
    @ui.button(label="Preleva", style=discord.ButtonStyle.red)
    async def pre(self, it, b): await it.response.send_modal(BankModal("pre"))

@bot.tree.command(name="telefono")
async def telefono(it: discord.Interaction):
    res = db_execute("SELECT numero_tel FROM utenti WHERE user_id=%s", (it.user.id,), fetch=True)
    num = res[0][0] if res else "".join(random.choices(string.digits, k=7))
    if not res: db_execute("INSERT INTO utenti (user_id, numero_tel) VALUES (%s,%s)", (it.user.id, num))
    await it.response.send_message(f"📱 iFruit | Numero: {num}", view=PhoneView(), ephemeral=True)

@bot.tree.command(name="portafoglio")
async def portafoglio(it: discord.Interaction):
    res = db_execute("SELECT contanti, banca, punti_patente FROM utenti WHERE user_id=%s", (it.user.id,), fetch=True)
    await it.response.send_message(f"👛 Contanti: {res[0][0]}€ | 🏦 Banca: {res[0][1]}€ | 🚗 Punti: {res[0][2]}/20")

# --- 🎒 INVENTARIO & SHOP ---
@bot.tree.command(name="crea_item_shop")
async def c_item(it: discord.Interaction, nome: str, prezzo: int, ruolo: discord.Role = None):
    rid = ruolo.id if ruolo else None
    db_execute("INSERT INTO shop_items VALUES (%s,%s,%s)", (nome, prezzo, rid))
    await it.response.send_message(f"✅ Item {nome} aggiunto.")

@bot.tree.command(name="compra")
async def compra(it: discord.Interaction, ricerca: str):
    res = db_execute("SELECT nome, prezzo FROM shop_items WHERE nome ILIKE %s", (f"%{ricerca}%",), fetch=True)
    if not res: return await it.response.send_message("❌ Nessun match.")
    if len(res) > 1:
        options = [discord.SelectOption(label=f"{n} ({p}€)", value=n) for n, p in res]
        view = ui.View()
        select = ui.Select(placeholder="Scegli l'item esatto...", options=options)
        async def callback(i: discord.Interaction):
            n_sel = select.values[0]
            prezzo = next(p for n, p in res if n == n_sel)
            db_execute("UPDATE utenti SET contanti=contanti-%s WHERE user_id=%s", (prezzo, i.user.id))
            db_execute("INSERT INTO inventario (user_id, item_nome) VALUES (%s,%s)", (i.user.id, n_sel))
            await i.response.send_message(f"🛒 Acquistato {n_sel}!")
        select.callback = callback
        view.add_item(select)
        await it.response.send_message("Troppe corrispondenze, seleziona:", view=view, ephemeral=True)
    else:
        n, p = res[0]
        db_execute("UPDATE utenti SET contanti=contanti-%s WHERE user_id=%s", (p, it.user.id))
        db_execute("INSERT INTO inventario (user_id, item_nome) VALUES (%s,%s)", (it.user.id, n))
        await it.response.send_message(f"🛒 Acquistato {n}!")

@bot.tree.command(name="inventario")
async def inv(it: discord.Interaction):
    res = db_execute("SELECT item_nome FROM inventario WHERE user_id=%s", (it.user.id,), fetch=True)
    await it.response.send_message(f"🎒 Inventario:\n" + "\n".join([f"- {r[0]}" for r in res]) if res else "Vuoto.")

@bot.tree.command(name="usa")
async def usa(it: discord.Interaction, item: str):
    res = db_execute("SELECT id FROM inventario WHERE user_id=%s AND item_nome=%s LIMIT 1", (it.user.id, item), fetch=True)
    if not res: return await it.response.send_message("❌ Non hai questo item.")
    db_execute("DELETE FROM inventario WHERE id=%s", (res[0][0],))
    await it.response.send_message(f"✨ Hai usato {item}.")

# --- 🏢 FAZIONI & DEPOSITO ---
@bot.tree.command(name="crea_fazione")
async def c_faz(it: discord.Interaction, nome: str, ruolo: discord.Role):
    db_execute("INSERT INTO fazioni (nome, ruolo_id) VALUES (%s,%s)", (nome, ruolo.id))
    await it.response.send_message(f"🏢 Fazione {nome} creata per {ruolo.name}.")

@bot.tree.command(name="deposito")
async def deposito(it: discord.Interaction):
    faz_disponibili = db_execute("SELECT nome, ruolo_id FROM fazioni", fetch=True)
    mie_faz = [f[0] for f in faz_disponibili if it.user.get_role(f[1])]
    if not mie_faz: return await it.response.send_message("❌ Non appartieni a nessuna fazione.")
    
    view = ui.View()
    select = ui.Select(placeholder="Scegli il deposito da aprire", options=[discord.SelectOption(label=n) for n in mie_faz])
    async def callback(i: discord.Interaction):
        f_sel = select.values[0]
        res = db_execute("SELECT fondo_cassa FROM fazioni WHERE nome=%s", (f_sel,), fetch=True)
        items = db_execute("SELECT item_nome FROM magazzino_fazione WHERE fazione_nome=%s", (f_sel,), fetch=True)
        msg = f"📦 **Deposito {f_sel}**\n💰 Fondo: {res[0][0]}€\n🎒 Items: {', '.join([r[0] for r in items]) if items else 'Nessuno'}"
        await i.response.send_message(msg, ephemeral=True)
    select.callback = callback
    view.add_item(select)
    await it.response.send_message("Seleziona fazione:", view=view, ephemeral=True)

# --- 🚗 MECCANICO & VEICOLI ---
@bot.tree.command(name="registra_veicolo")
async def reg_v(it: discord.Interaction, utente: discord.Member, modello: str):
    targa = "".join(random.choices(string.ascii_uppercase + string.digits, k=7))
    db_execute("INSERT INTO veicoli VALUES (%s,%s,%s)", (targa, utente.id, modello))
    chiave = f":chiavi: | Chiavi ({modello}) [{targa}]"
    db_execute("INSERT INTO inventario (user_id, item_nome) VALUES (%s,%s)", (utente.id, chiave))
    await it.response.send_message(f"🚗 {modello} registrato! Targa: {targa}")

@bot.tree.command(name="guida_veicolo")
async def guida(it: discord.Interaction):
    res = db_execute("SELECT modello, targa FROM veicoli WHERE owner_id=%s", (it.user.id,), fetch=True)
    if not res: return await it.response.send_message("❌ Non hai veicoli.")
    view = ui.View()
    select = ui.Select(options=[discord.SelectOption(label=f"{m} [{t}]", value=t) for m, t in res])
    select.callback = lambda i: i.response.send_message(f"🚘 Hai messo in moto il veicolo {select.values[0]}")
    view.add_item(select)
    await it.response.send_message("Scegli veicolo da guidare:", view=view, ephemeral=True)

# --- 👮 POLIZIA ---
@bot.tree.command(name="ammanetta")
async def ammanetta(it, utente: discord.Member): await it.response.send_message(f"👮 {it.user.name} ha ammanettato {utente.mention}.")
@bot.tree.command(name="smanetta")
async def smanetta(it, utente: discord.Member): await it.response.send_message(f"🔓 {it.user.name} ha smanettato {utente.mention}.")

@bot.tree.command(name="ricerca_cittadino")
async def ric_c(it, utente: discord.Member):
    res = db_execute("SELECT documento, punti_patente, patente FROM utenti WHERE user_id=%s", (utente.id,), fetch=True)
    await it.response.send_message(f"🔍 Dati {utente.name}:\n🪪 Doc: {res[0][0]}\n📉 Punti: {res[0][1]}\n🚗 Patente: {res[0][2]}")

# --- 🪪 DOCUMENTI ---
@bot.tree.command(name="crea_documento")
async def c_doc(it: discord.Interaction, nome: str, cognome: str):
    db_execute("UPDATE utenti SET documento=%s, patente='Valida', punti_patente=20 WHERE user_id=%s", (f"{nome} {cognome}", it.user.id))
    await it.response.send_message("🪪 Documento e Patente creati!")

# --- 🛠️ STAFF ---
@bot.tree.command(name="staff_aggiungi_item")
async def s_a_i(it, utente: discord.Member, item: str):
    db_execute("INSERT INTO inventario (user_id, item_nome) VALUES (%s,%s)", (utente.id, item))
    await it.response.send_message(f"✅ Staff ha dato {item} a {utente.name}")

@bot.tree.command(name="staff_aggiungi_soldi")
async def s_a_s(it, utente: discord.Member, soldi: int):
    db_execute("UPDATE utenti SET contanti=contanti+%s WHERE user_id=%s", (soldi, utente.id))
    await it.response.send_message(f"✅ Staff ha dato {soldi}€ a {utente.name}")

# --- 💸 SCAMBI & FATTURE ---
@bot.tree.command(name="dai_soldi")
async def dai_s(it, utente: discord.Member, soldi: int):
    db_execute("UPDATE utenti SET contanti=contanti-%s WHERE user_id=%s", (soldi, it.user.id))
    db_execute("UPDATE utenti SET contanti=contanti+%s WHERE user_id=%s", (soldi, utente.id))
    await it.response.send_message(f"💸 Hai dato {soldi}€ a {utente.name}")

@bot.tree.command(name="fattura")
async def fat(it, utente: discord.Member, euro: int, causale: str):
    db_execute("INSERT INTO fatture (emittente_id, destinatario_id, importo, causale) VALUES (%s,%s,%s,%s)", (it.user.id, utente.id, euro, causale))
    await it.response.send_message(f"📑 Fattura inviata a {utente.name}")

bot.run(TOKEN)
