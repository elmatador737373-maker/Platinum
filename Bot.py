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
from datetime import datetime, timedelta, time
import discord
from discord.ext import tasks
import asyncio

# ==========================================
# TASK AUTOMATICO GIORNALIERO (ORE 16:00 EUROPA)
# ==========================================
# Configurato alle 14:00 UTC (ovvero le 16:00 circa in Italia/Europa)
@tasks.loop(time=time(hour=14, minute=0))
async def controllo_finanziamenti():
    """Controlla i finanziamenti ogni giorno alle 16:00, gestisce i saldi insufficienti e invia i log."""
    risultati = await asyncio.to_thread(_elabora_finanziamenti_giornalieri)
    
    if not risultati or not bot.guilds:
        return
    guild = bot.guilds[0] 

    # 1. LOG DEI PAGAMENTI COMPLETI (Successo Totale)
    for pagato in risultati["pagati"]:
        uid = pagato["user_id"]
        quota = pagato["quota"]
        rimanenti = pagato["giorni_rimanenti"]
        
        embed = discord.Embed(
            title="✅ Quota Finanziamento Scalata",
            description=f"Il sistema ha prelevato con successo l'intera quota dall'utente <@{uid}>.",
            color=discord.Color.green(),
            timestamp=datetime.now()
        )
        embed.add_field(name="📉 Importo Prelevato", value=f"{quota:,.2f}€", inline=True)
        embed.add_field(name="📅 Giorni Rimanenti", value=f"{rimanents} giorni" if rimanenti > 0 else "🏁 ESTINTO!", inline=True)
        embed.set_footer(text="Riscossione Automatica")
        await invia_log_finanziario(guild, embed)

    # 2. LOG DEI PAGAMENTI PARZIALI (Utente con pochi soldi, conto svuotato + debito)
    for parziale in risultati["parziali"]:
        uid = parziale["user_id"]
        prelevato = parziale["prelevato"]
        mancante = parziale["mancante"]
        tot_debito = parziale["nuovo_debito_totale"]
        
        embed = discord.Embed(
            title="⚠️ Pagamento Parziale & Accumulo Debito",
            description=f"L'utente <@{uid}> non aveva abbastanza fondi. Il conto è stato svuotato.",
            color=discord.Color.orange(),
            timestamp=datetime.now()
        )
        embed.add_field(name="💸 Svuotato e Prelevato", value=f"{prelevato:,.2f}€", inline=True)
        embed.add_field(name="🚨 Quota Mancante Oggi", value=f"{mancante:,.2f}€", inline=True)
        embed.add_field(name="📊 Debito Totale Rimandato a Domani", value=f"**{tot_debito:,.2f}€**", inline=False)
        embed.set_footer(text="Conto Azzerato - Riscossione Coatta")
        await invia_log_finanziario(guild, embed)
        
        # Invia un DM di avviso all'utente insolvente
        try:
            user = await bot.fetch_user(int(uid))
            if user:
                await user.send(embed=embed)
        except Exception:
            pass


def _elabora_finanziamenti_giornalieri():
    db = get_db_connection()
    if not db:
        return None
        
    risultati_azione = {"pagati": [], "parziali": []}
    
    try:
        with db.cursor() as cursor:
            # Selezioniamo anche la nuova colonna 'debito_accumulato'
            cursor.execute("SELECT id, user_id, importo_giornaliero, giorni_rimanenti, debito_accumulato FROM public.finanziamenti WHERE giorni_rimanenti > 0;")
            finanziamenti = cursor.fetchall()
            
            for f in finanziamenti:
                f_id, user_id, quota_base, giorni_rimanenti, debito_accumulato = f
                
                # La quota totale da pagare oggi è: la quota del giorno + i debiti passati
                quota_totale_oggi = quota_base + debito_accumulato
                
                cursor.execute("SELECT bank FROM public.users WHERE user_id = %s;", (user_id,))
                res = cursor.fetchone()
                saldo_banca = res[0] if res else 0
                
                # CASO 1: L'utente ha abbastanza soldi per pagare TUTTO (Quota + Vecchi Debiti)
                if saldo_banca >= quota_totale_oggi:
                    cursor.execute("UPDATE public.users SET bank = bank - %s WHERE user_id = %s;", (quota_totale_oggi, user_id))
                    
                    # Il debito accumulato si azzera perché ha pagato tutto
                    nuovo_debito = 0
                    nuovi_giorni = giorni_rimanenti - 1
                    
                    if nuovi_giorni <= 0:
                        cursor.execute("DELETE FROM public.finanziamenti WHERE id = %s;", (f_id,))
                    else:
                        cursor.execute("UPDATE public.finanziamenti SET giorni_rimanenti = %s, debito_accumulato = %s WHERE id = %s;", (nuovi_giorni, nuovo_debito, f_id))
                    
                    risultati_azione["pagati"].append({
                        "user_id": user_id,
                        "quota": quota_totale_oggi,
                        "giorni_rimanenti": nuovi_giorni
                    })
                
                # CASO 2: I soldi non bastano. Il bot si prende tutto quello che trova e accumula il debito
                else:
                    prelevabile = saldo_banca if saldo_banca > 0 else 0
                    mancante_oggi = quota_totale_oggi - prelevabile
                    
                    # Svuota il conto dell'utente a 0
                    cursor.execute("UPDATE public.users SET bank = 0 WHERE user_id = %s;", (user_id,))
                    
                    # Il nuovo debito totale diventa quello che mancava oggi (che include già la quota base + i vecchi debiti)
                    nuovo_debito_totale = mancante_oggi
                    
                    # Scaliamo comunque il giorno, ma salviamo il debito rimanente per domani
                    nuovi_giorni = giorni_rimanenti - 1
                    
                    if nuovi_giorni <= 0 and nuovo_debito_totale <= 0:
                        cursor.execute("DELETE FROM public.finanziamenti WHERE id = %s;", (f_id,))
                    else:
                        # Se i giorni finiscono ma ha ancora debito, lasciamo il finanziamento attivo a 0 giorni finché non sana il debito
                        effettivi_giorni = nuovi_giorni if nuovi_giorni > 0 else 0
                        cursor.execute("UPDATE public.finanziamenti SET giorni_rimanenti = %s, debito_accumulato = %s WHERE id = %s;", (effettivi_giorni, nuovo_debito_totale, f_id))
                    
                    risultati_azione["parziali"].append({
                        "user_id": user_id,
                        "prelevato": prelevabile,
                        "mancante": quota_base - prelevabile if (quota_base - prelevabile) > 0 else 0,
                        "nuovo_debito_totale": nuovo_debito_totale
                    })
                    
            db.commit()
    except Exception as e:
        print(f"Errore nel database task finanziamenti: {e}")
    finally:
        db.close()
        
    return risultati_azione

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
# CONFIGURAZIONE STRUTTURE DATI TELEFONO
# ==========================================
INFO_APP_GRIGLIA = {
    "whatsapp": {"nome": "WhatsApp", "emoji": "💬", "stile": discord.ButtonStyle.green},
    "email": {"nome": "Mail", "emoji": "✉️", "stile": discord.ButtonStyle.blurple},
    "appstore": {"nome": "App Store", "emoji": "🛍️", "stile": discord.ButtonStyle.gray},
    "instagram": {"nome": "Instagram", "emoji": "📸", "stile": discord.ButtonStyle.secondary},
    "tiktok": {"nome": "TikTok", "emoji": "🎵", "stile": discord.ButtonStyle.secondary},
    "settings": {"nome": "Impostazioni", "emoji": "⚙️", "stile": discord.ButtonStyle.gray}
}

APP_STORE_DATA = {
    "instagram": {"nome": "Instagram", "prezzo": 0, "emoji": "📸", "desc": "Condividi foto e storie con la città."},
    "tiktok": {"nome": "TikTok", "prezzo": 0, "emoji": "🎵", "desc": "Guarda e pubblica video brevi RP."},
    "settings": {"nome": "Impostazioni", "prezzo": 0, "emoji": "⚙️", "desc": "Configura il tuo Evren OS."}
}

DIZIONARIO_COLORI = {
    "Grigio": discord.Color.from_rgb(47, 49, 54),
    "Blu": discord.Color.blue(),
    "Verde": discord.Color.green(),
    "Rosso": discord.Color.red(),
    "Oro": discord.Color.gold()
}

# ==========================================
# COMANDO PRINCIPALE /TELEFONO
# ==========================================
@bot.tree.command(name="telefono", description="Apri lo smartphone virtuale di Evren City RP")
async def telefono(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    
    user_id = str(interaction.user.id)
    username_discord = interaction.user.name

    numero, email, batteria, apps, sfondo = None, None, 100, [], "Grigio"

    conn = get_db_connection()
    if not conn:
        return await interaction.followup.send("❌ Errore critico: Il server della SIM non è raggiungibile.", ephemeral=True)

    try:
        cur = conn.cursor()
        # Aggiunta selezione della colonna 'sfondo' per la personalizzazione
        cur.execute("SELECT numero_telefono, email_indirizzo, batteria, app_installate, sfondo FROM telefono_sistema WHERE user_id = %s;", (user_id,))
        res = cur.fetchone()

        if not res:
            nuovo_numero = f"555-{random.randint(1000, 9999)}"
            nuova_email = f"{username_discord.lower().replace(' ', '')}@evren.city"
            apps_default = ['whatsapp', 'email', 'appstore', 'settings']
            
            cur.execute("""
                INSERT INTO telefono_sistema (user_id, numero_telefono, email_indirizzo, batteria, app_installate, sfondo) 
                VALUES (%s, %s, %s, 100, %s, 'Grigio') 
                ON CONFLICT (user_id) DO NOTHING;
            """, (user_id, nuovo_numero, nuova_email, apps_default))
            conn.commit()
            
            numero, email, batteria, apps, sfondo = nuovo_numero, nuova_email, 100, apps_default, "Grigio"
        else:
            numero, email, batteria, apps, sfondo = res

    except Exception as db_error:
        print(f"❌ [ERRORE SQL TELEFONO]: {db_error}")
        return await interaction.followup.send(f"❌ Errore di sincronizzazione SIM: {str(db_error)[:50]}", ephemeral=True)
    finally:
        if conn:
            cur.close()
            conn.close()

    try:
        embed = discord.Embed(
            title="📱 EVREN OS v14.5",
            description=f"🔋 **Batteria:** {batteria}%  •  📶 **Rete:** Evren 5G  •  🎨 **Tema:** {sfondo}\n"
                        f"⚙️ **Stato:** Dispositivo Sbloccato\n"
                        f"📊 **Memoria:** {len(apps)}/10 Applicazioni Installate\n"
                        f"──────────────────────────────────\n"
                        f"👤 **Proprietario:** {interaction.user.mention}\n"
                        f"📞 **SIM Card:** `{numero}`\n"
                        f"✉️ **ID Cloud:** `{email}`\n"
                        f"──────────────────────────────────\n"
                        f"✨ *Seleziona un'applicazione dalla griglia qui sotto per iniziare a navigare.*",
            color=DIZIONARIO_COLORI.get(sfondo, discord.Color.from_rgb(47, 49, 54))
        )
        embed.set_author(name="Smartphone Menu", icon_url=interaction.user.avatar.url if interaction.user.avatar else None)
        embed.set_footer(text="Evren City Cybernetics © 2026")

        view = SchermataHomeGrigliaView(user_id, apps)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        
    except Exception as gui_error:
        print(f"❌ [ERRORE INTERFACCIA TELEFONO]: {gui_error}")
        return await interaction.followup.send("❌ Errore grafico nel caricamento dello schermo.", ephemeral=True)


# ==========================================
# INTERFACCIA HOMESCREEN A GRIGLIA
# ==========================================
class SchermataHomeGrigliaView(discord.ui.View):
    def __init__(self, user_id=None, apps=None):
        super().__init__(timeout=None)
        self.user_id = user_id

        if apps is None:
            return

        for index, app_id in enumerate(apps):
            if app_id in INFO_APP_GRIGLIA:
                dati = INFO_APP_GRIGLIA[app_id]
                riga_destinazione = index // 2
                
                if riga_destinazione > 4: 
                    break
                    
                self.add_item(BottoneIconaGriglia(
                    label=dati["nome"], emoji=dati["emoji"], app_id=app_id, style=dati["stile"], row=riga_destinazione
                ))


class BottoneIconaGriglia(discord.ui.Button):
    def __init__(self, label, emoji, app_id, style, row):
        super().__init__(label=label, emoji=emoji, style=style, row=row)
        self.app_id = app_id

    async def callback(self, interaction: discord.Interaction):
        user_id = str(interaction.user.id)

        if self.app_id == "whatsapp":
            embed = discord.Embed(title="💬 WhatsApp Messenger", description="Benvenuto su WhatsApp. Comunica in tempo reale con i tuoi contatti salvati.\n\n⚠️ *Seleziona un'azione o scegli un contatto dal menu sotto.*", color=discord.Color.green())
            view = discord.ui.View()
            view.add_item(BottoneAzioneTelefono("Nuovo Contatto", "👤", "wa_add_contatto", discord.ButtonStyle.blurple))
            
            # Carica dinamicamente i contatti dal Database per il menu a tendina
            conn = get_db_connection()
            if conn:
                cur = conn.cursor()
                cur.execute("SELECT contatto_id, nome_salvato FROM telefono_contatti WHERE user_id = %s LIMIT 20;", (user_id,))
                contatti = cur.fetchall()
                cur.close()
                conn.close()
                if contatti:
                    view.add_item(SelectChatWhatsApp(contatti, user_id))
                else:
                    embed.description += "\n\n*La tua rubrica è vuota. Aggiungi un contatto per avviare una chat.*"

            view.add_item(BottoneHome(user_id))
            await interaction.response.edit_message(embed=embed, view=view)

        elif self.app_id == "email":
            embed = discord.Embed(title="✉️ Casella di Posta Mail", description="Gestione Mail Client.\n\n📥 **Posta in Arrivo:** Verranno mostrate le ultime mail indirizzate al tuo account.", color=discord.Color.blue())
            view = discord.ui.View()
            view.add_item(BottoneAzioneTelefono("Invia Nuova Mail", "📧", "mail_invia", discord.ButtonStyle.blurple))
            view.add_item(BottoneAzioneTelefono("Apri Inbox", "📥", "mail_inbox", discord.ButtonStyle.green))
            view.add_item(BottoneHome(user_id))
            await interaction.response.edit_message(embed=embed, view=view)

        elif self.app_id == "settings":
            embed = discord.Embed(title="⚙️ Impostazioni di Sistema", description="Configura i parametri del tuo dispositivo Evren OS.", color=discord.Color.greyple())
            view = discord.ui.View()
            view.add_item(SelectSfondoTelefono(user_id))
            view.add_item(BottoneAzioneTelefono("Ricarica Dispositivo (100%)", "⚡", "set_ricarica", discord.ButtonStyle.green))
            view.add_item(BottoneHome(user_id))
            await interaction.response.edit_message(embed=embed, view=view)

        elif self.app_id in ["instagram", "tiktok", "appstore"]:
            # Lasciamo intatti i comportamenti originali per le app social/store
            if self.app_id == "instagram":
                embed = discord.Embed(title="📸 Instagram Social", description="Condividi post e scatti fotografici RP.", color=discord.Color.magenta())
                view = discord.ui.View()
                view.add_item(BottoneAzioneTelefono("Crea Post", "🖼️", "ig_post", discord.ButtonStyle.danger))
            elif self.app_id == "tiktok":
                embed = discord.Embed(title="🎵 TikTok Evren", description="Carica o guarda clip video della community.", color=discord.Color.dark_theme())
                view = discord.ui.View()
                view.add_item(BottoneAzioneTelefono("Carica Video", "🎥", "tt_video", discord.ButtonStyle.secondary))
            elif self.app_id == "appstore":
                embed = discord.Embed(title="🛍️ App Store", description="Sblocca ed installa nuove applicazioni commerciali.", color=discord.Color.orange())
                view = discord.ui.View()
                view.add_item(MenuSelezionaAppStore(user_id))
            view.add_item(BottoneHome(user_id))
            await interaction.response.edit_message(embed=embed, view=view)


class BottoneAzioneTelefono(discord.ui.Button):
    def __init__(self, label, emoji, azione_id, style):
        super().__init__(label=label, emoji=emoji, style=style)
        self.azione_id = azione_id

    async def callback(self, interaction: discord.Interaction):
        user_id = str(interaction.user.id)
        
        if self.azione_id == "wa_add_contatto":
            await interaction.response.send_modal(ModalAggiungiContatto())
            
        elif self.azione_id == "mail_invia":
            await interaction.response.send_modal(ModalEmail())
            
        elif self.azione_id == "mail_inbox":
            await interaction.response.defer()
            conn = get_db_connection()
            if not conn:
                return await interaction.followup.send("❌ Errore di connessione.", ephemeral=True)
            
            try:
                cur = conn.cursor()
                cur.execute("SELECT email_indirizzo FROM telefono_sistema WHERE user_id = %s;", (user_id,))
                mia_mail = cur.fetchone()[0]

                cur.execute("SELECT mittente_email, oggetto, messaggio, data_invio FROM telefono_email WHERE destinatario_email = %s ORDER BY data_invio DESC LIMIT 5;", (mia_mail,))
                ricevute = cur.fetchall()
                
                embed = discord.Embed(title="📥 Posta in Arrivo", color=discord.Color.blue())
                if not ricevute:
                    embed.description = "*Nessuna email presente nella tua casella postale.*"
                else:
                    corpo = ""
                    for mail in ricevute:
                        mit, ogg, msg, data = mail
                        corpo += f"✉️ **Da:** `{mit}`\n📌 **Oggetto:** *{ogg}*\n> {msg}\n*Ricevuta il: {data.strftime('%d/%m %H:%M')}*\n──────────────────\n"
                    embed.description = corpo
                
                view = discord.ui.View()
                view.add_item(BottoneHome(user_id))
                await interaction.followup.send(embed=embed, view=view, ephemeral=True)
            except Exception as e:
                print(e)
            finally:
                cur.close()
                conn.close()

        elif self.azione_id == "set_ricarica":
            conn = get_db_connection()
            if conn:
                cur = conn.cursor()
                cur.execute("UPDATE telefono_sistema SET batteria = 100 WHERE user_id = %s;", (user_id,))
                conn.commit()
                cur.close()
                conn.close()
            await interaction.response.send_message("⚡ Dispositivo ricaricato al 100%!", ephemeral=True)
            
        elif self.azione_id in ["ig_post", "tt_video"]:
            await interaction.response.send_modal(ModalSocial(self.azione_id))


# ==========================================
# GESTIONE CONTATTI E CHAT DINAMICHE WHATSAPP
# ==========================================
class SelectChatWhatsApp(discord.ui.Select):
    def __init__(self, contatti, user_id):
        self.user_id = user_id
        opzioni = [
            discord.SelectOption(label=c[1], value=c[0], description=f"Apri la chat cifrata con questo utente", emoji="👤")
            for c in contatti
        ]
        super().__init__(placeholder="📖 Seleziona un contatto dalla tua rubrica...", options=opzioni)

    async def callback(self, interaction: discord.Interaction):
        target_id = self.values[0]
        nome_visualizzato = next((o.label for o in self.options if o.value == target_id), "Sconosciuto")

        conn = get_db_connection()
        if not conn:
            return await interaction.response.send_message("❌ Database irraggiungibile.", ephemeral=True)

        try:
            cur = conn.cursor()
            cur.execute("UPDATE telefono_chat SET letto = TRUE WHERE sender_id = %s AND receiver_id = %s;", (target_id, self.user_id))
            conn.commit()

            cur.execute("""
                SELECT sender_id, messaggio, letto, data_invio 
                FROM telefono_chat 
                WHERE (sender_id = %s AND receiver_id = %s) OR (sender_id = %s AND receiver_id = %s)
                ORDER BY data_invio DESC LIMIT 15;
            """, (self.user_id, target_id, target_id, self.user_id))
            
            messaggi = cur.fetchall()

            embed = discord.Embed(title=f"💬 Chat WhatsApp: {nome_visualizzato}", color=discord.Color.green())
            
            if not messaggi:
                embed.description = "*Nessun messaggio trovato. Avvia tu la conversazione inviando un testo.*"
            else:
                testo_chat = ""
                for msg in reversed(messaggi):
                    sender, testo, letto, data = msg
                    ora = data.strftime("%H:%M")
                    if sender == self.user_id:
                        spunta = " `✔️✔️`" if letto else " `✔️`"
                        testo_chat += f"🟢 **Tu** [{ora}]: {testo}{spunta}\n"
                    else:
                        testo_chat += f"⚪ **{nome_visualizzato}** [{ora}]: {testo}\n"
                embed.description = f"📋 **Cronologia Messaggi:**\n\n{testo_chat}"

            view = discord.ui.View()
            view.add_item(BottoneInviaMessaggioRapido(target_id, nome_visualizzato))
            view.add_item(BottoneHome(self.user_id))
            await interaction.response.edit_message(embed=embed, view=view)
        except Exception as e:
            print(e)
        finally:
            cur.close()
            conn.close()


# ==========================================
# SELEZIONE SFONDO NELLE IMPOSTAZIONI
# ==========================================
class SelectSfondoTelefono(discord.ui.Select):
    def __init__(self, user_id):
        self.user_id = user_id
        opzioni = [
            discord.SelectOption(label="Grigio", description="Sfondo Scuro Default OS", emoji="🌑"),
            discord.SelectOption(label="Blu", description="Tema Corporate Ocean", emoji="🔵"),
            discord.SelectOption(label="Verde", description="Tema Forest Smeraldo", emoji="🟢"),
            discord.SelectOption(label="Rosso", description="Tema Cyber Punk", emoji="🔴"),
            discord.SelectOption(label="Oro", description="Tema Luxury Edition", emoji="🟡")
        ]
        super().__init__(placeholder="🎨 Cambia lo sfondo del telefono...", options=opzioni)

    async def callback(self, interaction: discord.Interaction):
        sfondo_scelto = self.values[0]
        conn = get_db_connection()
        if conn:
            cur = conn.cursor()
            cur.execute("UPDATE telefono_sistema SET sfondo = %s WHERE user_id = %s;", (sfondo_scelto, self.user_id))
            conn.commit()
            cur.close()
            conn.close()
        await interaction.response.send_message(f"🎨 Sfondo aggiornato in: **{sfondo_scelto}**! Riapri il telefono per vedere le modifiche.", ephemeral=True)


# ==========================================
# MODAL INTEGRATI ED ESPANSI
# ==========================================
class ModalAggiungiContatto(discord.ui.Modal, title="Rubrica - Salva Contatto"):
    nome = discord.ui.TextInput(label="Nome Contatto RP", placeholder="Es: Mario Rossi")
    numero = discord.ui.TextInput(label="Numero di Telefono (555-XXXX)", placeholder="Es: 555-1234")

    async def on_submit(self, interaction: discord.Interaction):
        user_id = str(interaction.user.id)
        conn = get_db_connection()
        if not conn: return await interaction.response.send_message("Errore connessione.", ephemeral=True)
            
        try:
            cur = conn.cursor()
            cur.execute("SELECT user_id FROM telefono_sistema WHERE numero_telefono = %s;", (str(self.numero.value).strip(),))
            res = cur.fetchone()

            if not res:
                return await interaction.response.send_message("❌ Numero inesistente sulla rete Evren.", ephemeral=True)

            contatto_id = res[0]
            if contatto_id == user_id:
                return await interaction.response.send_message("❌ Non puoi salvare il tuo stesso numero in rubrica.", ephemeral=True)

            cur.execute("""
                INSERT INTO telefono_contatti (user_id, contatto_id, nome_salvato) 
                VALUES (%s, %s, %s) ON CONFLICT (user_id, contatto_id) DO UPDATE SET nome_salvato = EXCLUDED.nome_salvato;
            """, (user_id, contatto_id, str(self.nome.value)))
            conn.commit()
            await interaction.response.send_message(f"👤 Salvato in rubrica come: `{self.nome.value}`!", ephemeral=True)
        except Exception as e:
            print(e)
        finally:
            cur.close()
            conn.close()


class ModalEmail(discord.ui.Modal, title="Mail Client - Scrivi Messaggio"):
    dest_mail = discord.ui.TextInput(label="Email del Destinatario", placeholder="nome@evren.city")
    oggetto = discord.ui.TextInput(label="Oggetto della Mail", placeholder="Inserisci il titolo...")
    corpo = discord.ui.TextInput(label="Corpo del Messaggio", style=discord.TextStyle.paragraph, placeholder="Scrivi qui il testo formale...")

    async def on_submit(self, interaction: discord.Interaction):
        user_id = str(interaction.user.id)
        conn = get_db_connection()
        if not conn: return await interaction.response.send_message("Errore server mail.", ephemeral=True)
        
        try:
            cur = conn.cursor()
            # Trova l'indirizzo email del mittente
            cur.execute("SELECT email_indirizzo FROM telefono_sistema WHERE user_id = %s;", (user_id,))
            mia_mail = cur.fetchone()[0]

            # Controlla se il destinatario esiste effettivamente
            cur.execute("SELECT user_id FROM telefono_sistema WHERE email_indirizzo = %s;", (str(self.dest_mail.value).strip().lower(),))
            dest_esiste = cur.fetchone()

            if not dest_esiste:
                return await interaction.response.send_message("❌ L'indirizzo inserito non è registrato sul server mail della città.", ephemeral=True)

            # Inserisce effettivamente l'email nel database
            cur.execute("INSERT INTO telefono_email (mittente_email, destinatario_email, oggetto, messaggio) VALUES (%s, %s, %s, %s);", 
                        (mia_mail, str(self.dest_mail.value).strip().lower(), str(self.oggetto.value), str(self.corpo.value)))
            conn.commit()
            await interaction.response.send_message(f"📧 Mail recapitata correttamente a `{self.dest_mail.value}`!", ephemeral=True)
        except Exception as e:
            print(e)
        finally:
            cur.close()
            conn.close()


class BottoneInviaMessaggioRapido(discord.ui.Button):
    def __init__(self, target_id, nome_visualizzato):
        super().__init__(label="Invia Messaggio", emoji="📝", style=discord.ButtonStyle.green)
        self.target_id = target_id
        self.nome_visualizzato = nome_visualizzato

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(ModalRispostaWhatsApp(self.target_id, self.nome_visualizzato))


class ModalRispostaWhatsApp(discord.ui.Modal):
    def __init__(self, target_id, nome_visualizzato):
        super().__init__(title=f"Invia a {nome_visualizzato}")
        self.target_id = target_id
        self.nome_visualizzato = nome_visualizzato
        self.msg = discord.ui.TextInput(label="Testo Messaggio", style=discord.TextStyle.paragraph, placeholder="Scrivi il testo da inviare...")
        self.add_item(self.msg)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        mittente_id = str(interaction.user.id)
        testo = str(self.msg.value)

        conn = get_db_connection()
        if not conn: return

        try:
            cur = conn.cursor()
            cur.execute("INSERT INTO telefono_chat (sender_id, receiver_id, messaggio, letto) VALUES (%s, %s, %s, FALSE);", (mittente_id, self.target_id, testo))
            conn.commit()

            try:
                target_user = await interaction.client.fetch_user(int(self.target_id))
                embed_dm = discord.Embed(title="💬 WhatsApp RP", description=f"✉️ **Nuovo messaggio da un numero in Rubrica:**\n\n> {testo}", color=discord.Color.green())
                await target_user.send(embed=embed_dm)
            except:
                pass
            await interaction.followup.send(f"✔️ Messaggio consegnato a {self.nome_visualizzato}!", ephemeral=True)
        except Exception as e:
            print(e)
        finally:
            cur.close()
            conn.close()


class ModalSocial(discord.ui.Modal, title="Condividi sui Social"):
    contenuto = discord.ui.TextInput(label="Descrizione", placeholder="Cosa stai pensando?")
    media_url = discord.ui.TextInput(label="Link Foto/Video URL", required=False)

    def __init__(self, tipo):
        super().__init__()
        self.tipo = tipo

    async def on_submit(self, interaction: discord.Interaction):
        piattaforma = "Instagram 📸" if self.tipo == "ig_post" else "TikTok 🎵"
        colore = discord.Color.magenta() if self.tipo == "ig_post" else discord.Color.default()
        embed = discord.Embed(title=f"📱 Feed {piattaforma}", description=f"**Profilo: {interaction.user.mention}**\n\n{self.contenuto.value}", color=colore)
        if self.media_url.value:
            embed.set_image(url=self.media_url.value)
        await interaction.response.send_message(embed=embed)


# ==========================================
# COMPONENTI DI NAVIGAZIONE E APP STORE
# ==========================================
class MenuSelezionaAppStore(discord.ui.Select):
    def __init__(self, user_id):
        self.user_id = user_id
        opzioni = [
            discord.SelectOption(label=info["nome"], value=key, description=f"Prezzo: €{info['prezzo']} | {info['desc']}", emoji=info["emoji"])
            for key, info in APP_STORE_DATA.items()
        ]
        super().__init__(placeholder="Seleziona un software da acquistare...", options=opzioni)

    async def callback(self, interaction: discord.Interaction):
        app_scelta = self.values[0]
        info = APP_STORE_DATA[app_scelta]
        costo = info["prezzo"]

        conn = get_db_connection()
        if not conn: return

        try:
            cur = conn.cursor()
            cur.execute("SELECT app_installate FROM telefono_sistema WHERE user_id = %s;", (self.user_id,))
            apps = cur.fetchone()[0]

            if app_scelta in apps:
                return await interaction.response.send_message(f"❌ {info['nome']} è già sul tuo smartphone.", ephemeral=True)

            cur.execute("SELECT bank FROM users WHERE id = %s;", (self.user_id,))
            res_bank = cur.fetchone()
            saldo_banca = res_bank[0] if res_bank else 0

            if saldo_banca < costo:
                return await interaction.response.send_message(f"❌ Fondi bancari insufficienti.", ephemeral=True)

            cur.execute("UPDATE users SET bank = bank - %s WHERE id = %s;", (costo, self.user_id))
            cur.execute("UPDATE telefono_sistema SET app_installate = array_append(app_installate, %s) WHERE user_id = %s;", (app_scelta, self.user_id))
            conn.commit()

            await interaction.response.send_message(f"📥 Installata: **{info['nome']}**!", ephemeral=True)
        except Exception as e:
            print(e)
        finally:
            cur.close()
            conn.close()


class BottoneHome(discord.ui.Button):
    def __init__(self, user_id):
        super().__init__(label="Home Screen", emoji="🏠", style=discord.ButtonStyle.danger)
        self.user_id = user_id

    async def callback(self, interaction: discord.Interaction):
        conn = get_db_connection()
        if not conn: return
            
        try:
            cur = conn.cursor()
            cur.execute("SELECT numero_telefono, email_indirizzo, batteria, app_installate, sfondo FROM telefono_sistema WHERE user_id = %s;", (self.user_id,))
            numero, email, batteria, apps, sfondo = cur.fetchone()

            embed = discord.Embed(
                title="📱 EVREN OS v14.5",
                description=f"🔋 **Batteria:** {batteria}%  •  📶 **Rete:** Evren 5G  •  🎨 **Tema:** {sfondo}\n"
                            f"⚙️ **Stato:** Dispositivo Sbloccato\n"
                            f"📊 **Memoria:** {len(apps)}/10 Applicazioni Installate\n"
                            f"──────────────────────────────────\n"
                            f"👤 **Proprietario:** {interaction.user.mention}\n"
                            f"📞 **SIM Card:** `{numero}`\n"
                            f"✉️ **ID Cloud:** `{email}`\n"
                            f"──────────────────────────────────\n"
                            f"✨ *Seleziona un'applicazione dalla griglia qui sotto per iniziare a navigare.*",
                color=DIZIONARIO_COLORI.get(sfondo, discord.Color.from_rgb(47, 49, 54))
            )
            embed.set_author(name="Smartphone Menu", icon_url=interaction.user.avatar.url if interaction.user.avatar else None)
            embed.set_footer(text="Evren City Cybernetics © 2026")

            await interaction.response.edit_message(embed=embed, view=SchermataHomeGrigliaView(self.user_id, apps))
        except Exception as e:
            print(e)
        finally:
            cur.close()
            conn.close()

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
from datetime import datetime, timedelta, time

# ==========================================
# BACKGROUND TASK: CONTROLLO SCADENZE E AVVISI DM (CORRETTO - ORE FIXED)
# ==========================================
# Il task ignora i riavvii di Render: si attiverà SOLO quando l'orologio segna le 14:00 UTC (16:00 ITA)
@tasks.loop(time=time(hour=14, minute=0))
async def controllo_scadenze_veicoli():
    conn = get_db_connection()
    if not conn:
        print("❌ [TASK SCADENZE] Errore di connessione al database.")
        return

    def elabora_scadenze_e_avvisi():
        oggi = datetime.now().date()
        soglia_7gg = oggi + timedelta(days=7)
        domani = oggi + timedelta(days=1)
        
        avvisi_dm = [] # Lista di tuple: (owner_id, targa, tipo_scadenza, giorni_mancanti)

        with conn.cursor() as cursor:
            # Query pulita: seleziona i veicoli che hanno almeno una scadenza impostata
            cursor.execute("""
                SELECT targa, data_scadenza_assicurazione, data_scadenza_revisione, owner_id 
                FROM public.veicoli 
                WHERE data_scadenza_assicurazione IS NOT NULL 
                   OR data_scadenza_revisione IS NOT NULL;
            """)
            veicoli = cursor.fetchall()
            
            for targa, scadenza_ass, scadenza_rev, owner_id in veicoli:
                if not owner_id:
                    continue

                # --- Controllo Assicurazione ---
                if scadenza_ass:
                    # 'scadenza_ass' è già un oggetto datetime.date grazie a psycopg2
                    if scadenza_ass == soglia_7gg:
                        avvisi_dm.append((owner_id, targa, "l'Assicurazione", 7))
                    elif scadenza_ass == domani:
                        avvisi_dm.append((owner_id, targa, "l'Assicurazione", 1))
                    elif scadenza_ass <= oggi:
                        avvisi_dm.append((owner_id, targa, "l'Assicurazione (GIÀ SCADUTA!)", 0))
                
                # --- Controllo Revisione ---
                if scadenza_rev:
                    # 'scadenza_rev' è già un oggetto datetime.date grazie a psycopg2
                    if scadenza_rev == soglia_7gg:
                        avvisi_dm.append((owner_id, targa, "la Revisione", 7))
                    elif scadenza_rev == domani:
                        avvisi_dm.append((owner_id, targa, "la Revisione", 1))
                    elif scadenza_rev <= oggi:
                        avvisi_dm.append((owner_id, targa, "la Revisione (GIÀ SCADUTA!)", 0))
                
        conn.close()
        return avvisi_dm

    # Esegui i controlli sul database in un thread separato per non bloccare il bot
    lista_avvisi = await asyncio.to_thread(elabora_scadenze_e_avvisi)

    # --- Invio dei messaggi in DM agli utenti ---
    for user_id, targa, tipo, giorni in lista_avvisi:
        try:
            user = await bot.fetch_user(int(user_id))
            if user:
                # Personalizza il testo e la gravità dell'embed in base ai giorni rimasti
                if giorni == 0:
                    desc_testo = f"Ciao {user.display_name}, ti avvisiamo che **{tipo}** del tuo veicolo è **scaduta**! Mettiti in regola al più presto."
                    colore_embed = discord.Color.dark_red()
                    stato_testo = "🔴 SCADUTO"
                elif giorni == 1:
                    desc_testo = f"Ciao {user.display_name}, ti avvisiamo che **domani** scadrà **{tipo}** del tuo veicolo."
                    colore_embed = discord.Color.red()
                    stato_testo = "⏳ Scade tra 24 ore"
                else:
                    desc_testo = f"Ciao {user.display_name}, ti ricordiamo che tra **7 giorni** scadrà **{tipo}** del tuo veicolo."
                    colore_embed = discord.Color.orange()
                    stato_testo = "⚠️ Scadenza a breve (7 giorni)"

                embed_dm = discord.Embed(
                    title="⚠️ Avviso Scadenza Veicolo",
                    description=desc_testo,
                    color=colore_embed,
                    timestamp=datetime.now()
                )
                embed_dm.add_field(name="🚘 Veicolo", value=f"Targa: `{targa}`", inline=True)
                embed_dm.add_field(name="📌 Stato", value=stato_testo, inline=True)
                embed_dm.set_footer(text="Notifiche automatiche Motorizzazione")
                
                await user.send(embed=embed_dm)
        except discord.Forbidden:
            print(f"⚠️ Impossibile inviare DM all'utente {user_id} (DMs chiusi).")
        except Exception as e:
            print(f"❌ Errore nell'invio del DM a {user_id}: {e}")

    print("🔄 [TASK SCADENZE] Controllo scadenze e invio avvisi DM completato.")



# ==========================================
# BOT TREE: COMANDO ASSICURAZIONE (CORRETTO - 7 GIORNI)
# ==========================================
@bot.tree.command(name="assicurazione", description="Rinnova o imposta l'assicurazione attiva di un veicolo tramite targa (Validità: 7 giorni)")
@app_commands.describe(targa="La targa del veicolo da assicurare")
async def assicurazione(interaction: discord.Interaction, targa: str):
    user_roles_ids = [role.id for role in interaction.user.roles]
    if RUOLO_ASSICURAZIONE_ID not in user_roles_ids:
        return await interaction.response.send_message("⛔ Non hai il ruolo staff autorizzato per gestire l'Assicurazione.", ephemeral=True)

    targa_pulita = targa.upper()
    
    # Data per il DB (oggetto date nativo) e per la risposta testuale
    data_scadenza_db = (datetime.now() + timedelta(days=7)).date()
    data_stampa = data_scadenza_db.strftime("%d/%m/%Y")
    
    conn = get_db_connection()
    if not conn:
        return await interaction.response.send_message("❌ Errore tecnico di connessione al database.", ephemeral=True)

    def update_assicurazione():
        with conn.cursor() as cursor:
            cursor.execute("SELECT 1 FROM public.veicoli WHERE targa = %s;", (targa_pulita,))
            if not cursor.fetchone():
                return "not_found"
            # Rimosso il campo finto 'assicurato = true'
            cursor.execute("UPDATE public.veicoli SET data_scadenza_assicurazione = %s WHERE targa = %s;", (data_scadenza_db, targa_pulita))
        conn.commit()
        return "success"

    risultato = await asyncio.to_thread(update_assicurazione)
    conn.close()

    if risultato == "not_found":
        await interaction.response.send_message(f"❌ Nessun veicolo associato alla targa `{targa_pulita}` nella tabella `public.veicoli`.", ephemeral=True)
    else:
        await interaction.response.send_message(f"✅ **Assicurazione Aggiornata**: Il veicolo con targa **{targa_pulita}** è ora assicurato fino al `{data_stampa}`.")


# ==========================================
# CONFIGURAZIONE RUOLI AUTORIZZATI (Esempio)
# ==========================================
RUOLO_REVISIONE_1_ID = 1253460183053504582  # Sostituisci con il primo ID ruolo
RUOLO_REVISIONE_2_ID = 1257782342504812688  # Sostituisci con il secondo ID ruolo

# ==========================================
# BOT TREE: COMANDO REVISIONE (CORRETTO - 7 GIORNI)
# ==========================================
@bot.tree.command(name="revisione", description="Rinnova o imposta la revisione statale di un veicolo tramite targa (Validità: 7 giorni)")
@app_commands.describe(targa="La targa del veicolo da revisionare")
async def revisione(interaction: discord.Interaction, targa: str):
    user_roles_ids = [role.id for role in interaction.user.roles]
    
    # Controlla che l'utente non sia privo di ENTRAMBI i ruoli autorizzati
    if RUOLO_REVISIONE_1_ID not in user_roles_ids and RUOLO_REVISIONE_2_ID not in user_roles_ids:
        return await interaction.response.send_message("⛔ Non hai il ruolo staff autorizzato per gestire la Revisione.", ephemeral=True)

    targa_pulita = targa.upper()
    
    # Data per il DB (oggetto date nativo) e per la risposta testuale
    data_scadenza_db = (datetime.now() + timedelta(days=7)).date()
    data_stampa = data_scadenza_db.strftime("%d/%m/%Y")
    
    conn = get_db_connection()
    if not conn:
        return await interaction.response.send_message("❌ Errore tecnico di connessione al database.", ephemeral=True)

    def update_revisione():
        with conn.cursor() as cursor:
            cursor.execute("SELECT 1 FROM public.veicoli WHERE targa = %s;", (targa_pulita,))
            if not cursor.fetchone():
                return "not_found"
            # Rimosso il campo finto 'revisionato = true'
            cursor.execute("UPDATE public.veicoli SET data_scadenza_revisione = %s WHERE targa = %s;", (data_scadenza_db, targa_pulita))
        conn.commit()
        return "success"

    risultato = await asyncio.to_thread(update_revisione)
    conn.close()

    if risultato == "not_found":
        await interaction.response.send_message(f"❌ Nessun veicolo associato alla targa `{targa_pulita}` nella tabella `public.veicoli`.", ephemeral=True)
    else:
        await interaction.response.send_message(f"✅ **Revisione Aggiornata**: Il veicolo con targa **{targa_pulita}** è stato revisionato con successo fino al `{data_stampa}`.")


# ==========================================
# BOT TREE: COMANDO MECCANICO (GESTIONE MODIFICHE VEICOLO)
# ==========================================
@bot.tree.command(name="veicolo_mod", description="Permette ai meccanici di installare o rimuovere modifiche da un veicolo")
@app_commands.describe(
    targa="La targa del veicolo da modificare",
    azione="Scegli se aggiungere o rimuovere la modifica",
    modifica="Il nome della modifica (es. Motore Step 3, Turbo, Estetica)"
)
@app_commands.choices(azione=[
    app_commands.Choice(name="🔧 Installa Modifica", value="aggiungi"),
    app_commands.Choice(name="🗑️ Rimuovi Modifica", value="rimuovi")
])
async def veicolo_mod(interaction: discord.Interaction, targa: str, azione: str, modifica: str):
    # Controllo dei ruoli per l'accesso (Sostituisci RUOLO_MECCANICO_ID con l'ID reale del tuo ruolo)
    user_roles_ids = [role.id for role in interaction.user.roles]
    
  
    if RUOLO_REVISIONE_1_ID not in user_roles_ids and RUOLO_REVISIONE_2_ID not in user_roles_ids:
        return await interaction.response.send_message(
            "⛔ Non hai il ruolo lavorativo adatto (Meccanico) per eseguire questa operazione.", 
            ephemeral=True
        )

    targa_pulita = targa.upper().strip()
    modifica_pulita = modifica.strip()
    
    if not modifica_pulita:
        return await interaction.response.send_message("❌ Devi specificare una modifica valida!", ephemeral=True)

    await interaction.response.defer(ephemeral=False)

    conn = get_db_connection()
    if not conn:
        return await interaction.followup.send("❌ Errore tecnico di connessione al database.", ephemeral=True)

    try:
        cur = conn.cursor()
        
        # 1. Verifichiamo se il veicolo esiste e prendiamo le modifiche attuali
        cur.execute("SELECT modello, modifiche, owner_id FROM public.veicoli WHERE targa = %s;", (targa_pulita,))
        veicolo_data = cur.fetchone()
        
        if not veicolo_data:
            cur.close()
            conn.close()
            return await interaction.followup.send(f"❌ Nessun veicolo trovato con targa `{targa_pulita}`.", ephemeral=True)
            
        modello, modifiche_raw, owner_id = veicolo_data
        
        # Gestiamo la stringa delle modifiche trasformandola in una lista pulita
        lista_modifiche = [m.strip() for m in modifiche_raw.split(",") if m.strip()] if modifiche_raw else []

        # 2. Logica di aggiunta o rimozione
        if azione == "aggiungi":
            # Evitiamo duplicati identici (es. due Turbo uguali)
            if modifica_pulita.lower() in [m.lower() for m in lista_modifiche]:
                cur.close()
                conn.close()
                return await interaction.followup.send(
                    f"⚠️ La modifica **{modifica_pulita}** risulta già installata su questo veicolo!", 
                    ephemeral=True
                )
            
            lista_modifiche.append(modifica_pulita)
            titolo_embed = "🛠| MODIFICA INSTALLATA CON SUCCESSO"
            colore_embed = discord.Color.green()
            descrizione_embed = f"Il meccanico {interaction.user.mention} ha installato una nuova modifica."
            testo_campo = "Componente Montato"
            
        elif azione == "rimuovi":
            # Cerchiamo la modifica ignorando maiuscole/minuscole
            trovata = False
            for m in lista_modifiche:
                if m.lower() == modifica_pulita.lower():
                    lista_modifiche.remove(m)
                    trovata = True
                    break
            
            if not trovata:
                cur.close()
                conn.close()
                return await interaction.followup.send(
                    f"❌ La modifica **{modifica_pulita}** non è attualmente installata su questo veicolo.", 
                    ephemeral=True
                )
                
            titolo_embed = "🔧| MODIFICA RIMOSSA CON SUCCESSO"
            colore_embed = discord.Color.red()
            descrizione_embed = f"Il meccanico {interaction.user.mention} ha rimosso un componente dal veicolo."
            testo_campo = "Componente Smontato"

        # 3. Aggiorniamo il database con la nuova lista modifiche
        nuove_modifiche_str = ", ".join(lista_modifiche)
        cur.execute("UPDATE public.veicoli SET modifiche = %s WHERE targa = %s;", (nuove_modifiche_str, targa_pulita))
        conn.commit()

        # 4. Creiamo il resoconto visivo per l'officina/utente
        embed = discord.Embed(
            title=titolo_embed,
            description=descrizione_embed,
            color=colore_embed,
            timestamp=datetime.now()
        )
        embed.add_field(name="🚘 Veicolo", value=f"**{modello}** (`{targa_pulita}`)", inline=True)
        embed.add_field(name="👤 Proprietario", value=f"<@{owner_id}>" if owner_id else "Nessuno", inline=True)
        embed.add_field(name=testo_campo, value=f"```\n{modifica_pulita}\n```", inline=False)
        
        # Mostriamo lo stato attuale di tutte le modifiche del veicolo
        stato_attuale = ", ".join(lista_modifiche) if lista_modifiche else "*Nessuna modifica installata*"
        embed.add_field(name="📊 Modifiche Attuali sul Veicolo", value=f"```{stato_attuale}```", inline=False)
        
        embed.set_footer(text=f"Officina Autorizzata | Operatore: {interaction.user.display_name}")
        await interaction.followup.send(embed=embed)

    except Exception as e:
        if 'conn' in locals() and conn:
            conn.rollback()
        print(f"[ERROR] Errore durante l'aggiornamento modifiche veicolo: {e}")
        await interaction.followup.send("❌ Errore tecnico durante l'elaborazione dell'ordine di lavoro.", ephemeral=True)
    finally:
        if 'cur' in locals() and cur:
            cur.close()
        if 'conn' in locals() and conn:
            conn.close()


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

    # # 3. LOGS FINANZIARI (Eseguiti fuori dal blocco Try/Finally del DB per evitare conflitti)
    if successo:
        try:
            # Creazione dell'embed del log
            emb = discord.Embed(
                title="💵 LOG BONIFICO", 
                color=discord.Color.green(), 
                timestamp=discord.utils.utcnow()
            )
            emb.add_field(name="Mittente", value=interaction.user.mention)
            emb.add_field(name="Destinatario", value=utente.mention)
            emb.add_field(name="Importo", value=f"{ammontare}$") 
            
            # 1. Primo log (Canale finanziario predefinito)
            await invia_log_finanziario(interaction.guild, emb)
            
            # 2. Secondo log (Canale specifico a parte)
            ID_CANALE_SEPARATO = 1482758643773341848
            canale_separato = interaction.guild.get_channel(ID_CANALE_SEPARATO)
            
            # Se il canale non è in cache, proviamo a recuperarlo direttamente da Discord
            if not canale_separato:
                try:
                    canale_separato = await interaction.guild.fetch_channel(ID_CANALE_SEPARATO)
                except Exception:
                    canale_separato = None
                    
            if canale_separato:
                await canale_separato.send(embed=emb)
            else:
                print(f"[WARNING] Impossibile trovare il canale con ID {ID_CANALE_SEPARATO} per il log separato.")
                
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

# ================= CONFIGURAZIONI AGGIUNTIVE TELEFONO =================
GUILD_ID = 1233353915559313478 # METTI QUI L'ID DEL TUO SERVER DISCORD RP

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
    
    # 1. REGISTRAZIONE DELLA VIEW PERSISTENTE DEL TELEFONO
    # Serve a mantenere i bottoni del telefono funzionanti dopo il riavvio del bot
    try:
        bot.add_view(SchermataHomeGrigliaView(user_id=None, apps=None))
        print("📱 [VIEW] Interfaccia persistente Telefono caricata con successo!")
    except Exception as e:
        print(f"❌ Errore caricamento View persistente: {e}")
    
    # 2. SINCRONIZZAZIONE LOCALE ISTANTANEA (Sostituisce il vecchio sync globale lento)
    try:
        print("🔄 Sincronizzazione comandi slash locale sul server...")
        guild = discord.Object(id=GUILD_ID)
        
        # Copia i comandi globali all'interno del tuo server specifico
        bot.tree.copy_global_to(guild=guild)
        
        # Sincronizza l'albero dei comandi sul server
        synced = await bot.tree.sync(guild=guild)
        print(f"🔄 Sincronizzati {len(synced)} comandi in modo istantaneo nel server {GUILD_ID}!")
    except Exception as e:
        print(f"❌ Errore durante la sincronizzazione locale: {e}")

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
