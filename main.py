import os
import csv
import httpx
import asyncio
from io import StringIO
from fastapi import FastAPI
from pydantic import BaseModel
from dotenv import load_dotenv
from typing import Optional
from collections import defaultdict

from datetime import datetime, timedelta, timezone, time, date

load_dotenv()

app = FastAPI()

BOT_USERNAME = os.getenv("BOT_USERNAME")
BOT_TOKEN = os.getenv("BOT_TOKEN")
CSV_URL = os.getenv("CSV_URL")
SHEET_LINK = os.getenv("SHEET_LINK")
RENDER_URL = os.getenv("RENDER_URL")
AUTHORIZED_CHAT_IDS = list(
    map(int, os.getenv("AUTHORIZED_CHAT_IDS", "").split(","))
)

if not BOT_TOKEN or not CSV_URL:
    raise RuntimeError("Set BOT_TOKEN and CSV_URL in .env file")

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

user_states = {}

# ======= Due-date reminders config =======
TZ = timezone(timedelta(hours=-3))
REMINDER_HOURS = [9]
REMINDER_DAYS = {1}
sent_reminders = set()


class TelegramUpdate(BaseModel):
    update_id: int
    message: Optional[dict] = None


async def fetch_csv_data():
    """Fetch CSV from URL and parse into list of dicts by header."""
    async with httpx.AsyncClient(follow_redirects=True) as client:
        response = await client.get(CSV_URL)
        response.raise_for_status()
        f = StringIO(response.text)
        reader = csv.DictReader(f)
        return list(reader)


def parse_brl_to_float(value: str) -> float:
    """Convert Brazilian Real formatted string like 'R$ 1.234,56' to float 1234.56"""
    if not value:
        return 0.0
    value = value.replace("R$", "").replace(".", "").replace(",", ".").strip()
    try:
        return float(value)
    except ValueError:
        return 0.0


def format_currency(value: float) -> str:
    """Format float to BRL currency string."""
    return f"R$ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


async def send_message(chat_id: int, text: str, reply_markup=None):
    data = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    if reply_markup:
        data["reply_markup"] = reply_markup
    async with httpx.AsyncClient() as client:
        await client.post(f"{TELEGRAM_API}/sendMessage", json=data)


# App init response
@app.get("/")
async def health_check():
    return {"status": "ok"}


# Avoid Render cold start
@app.get("/ping")
async def ping():
    return {"status": "ok"}


@app.post(f"/webhook/{BOT_TOKEN}")
async def telegram_webhook(update: TelegramUpdate):
    if not update.message:
        return {"ok": True}

    chat_id = update.message["chat"]["id"]
    text = update.message.get("text", "").strip()
    user_id = update.message["from"]["id"]

    if chat_id not in AUTHORIZED_CHAT_IDS:
        await send_message(
            chat_id,
            "❌ Oops!\n\nI'm sorry, but I don't recognize your ID.\nOnly authorized users can access this feature.\n"
            + "If you think this is a mistake, feel free to reach out and I'll take a look for you. 💁‍♀️"
            + "\n\n"
            + "❌ Opa!\n\nDesculpa, mas não reconheço o seu ID.\nApenas usuários autorizados podem acessar este recurso.\n"
            + "Se você acha que isso foi um engano, é só me chamar que eu dou uma olhadinha pra você. 💁‍♀️",
        )
        return {"ok": True}

    # Handle /faturas command
    elif text.lower() in ["/faturas", f"/faturas@{BOT_USERNAME}"]:
        rows = await fetch_csv_data()

        # Filter cards data
        cards = [
            r
            for r in rows
            if r["CARTÃO"].strip().upper() in {"NUBANK", "INTER", "SANTANDER", "MERCPAGO"}
        ]

        # Find summary rows
        total_final = next(
            (r for r in rows if r["CARTÃO"].strip().upper() == "TOTAL FINAL"), None
        )
        a_pagar = next(
            (r for r in rows if r["CARTÃO"].strip().upper() == "A PAGAR"), None
        )

        totais_por_pessoa = defaultdict(float)

        for row in rows:
            pessoa = row.get("PESSOA", "").strip()
            valor_str = row.get("VALOR (R$)", "").strip()
            if pessoa and valor_str:
                valor = parse_brl_to_float(valor_str)
                totais_por_pessoa[pessoa] += valor

        if not cards:
            await send_message(
                chat_id, "Não foi possível encontrar os dados dos cartões na planilha."
            )
            return {"ok": True}

        # Format message
        msg_lines = [f"💳 *Faturas do mês: {cards[0]['MÊS'].strip()}*\n"]

        # Colors per bank
        emojis = {"NUBANK": "🟣", "INTER": "🟠", "SANTANDER": "🔴", "MERCPAGO": "⚪️"}

        # Bills per card
        for card in cards:
            nome = card["CARTÃO"].strip().upper()
            total = parse_brl_to_float(card["TOTAL"])
            emoji = emojis.get(nome, "💳")
            msg_lines.append(f"{emoji} *{nome.title()}*: {format_currency(total)}")

        # Add summaries if found
        if total_final:
            total = parse_brl_to_float(total_final["TOTAL"])
            msg_lines.append(f"\n💸 *TOTAL FINAL:* {format_currency(total)}")
        if a_pagar:
            total = parse_brl_to_float(a_pagar["TOTAL"])
            msg_lines.append(f"💰 *A PAGAR:* {format_currency(total)}")

        if totais_por_pessoa:
            # Emoji per person
            pessoa_emojis = ["👩‍🏫💼", "👩✨", "👨‍💻🎮"]
            msg_lines.append("\n🔍 *POR PESSOA:*")
            for i, (pessoa, total) in enumerate(totais_por_pessoa.items()):
                emoji = pessoa_emojis[i] if i < len(pessoa_emojis) else "👤"
                msg_lines.append(f"{emoji} *{pessoa}*: {format_currency(total)}")

        # Add status and deadline info
        msg_lines.append("\n📅 *STATUS E VENCIMENTO:*")

        status_emojis = {"ABERTA": "📌", "PAGA": "✅", "ATRASADA": "⚠️"}

        for card in cards:
            nome = card["CARTÃO"].strip().upper()
            venc = card["D. VENC"].strip()
            status = card["SITUAÇÃO"].strip().upper()
            emoji = emojis.get(nome, "💳")
            status_emote = status_emojis.get(status, "📌")
            msg_lines.append(
                f"- {emoji} *{nome.title()}*\n"
                f"  📆 Vencimento: *Dia {venc}*\n"
                f"  {status_emote} Situação: *{status}*\n"
            )

        msg_lines.append(
            f"\n📊 Link para detalhamento de faturas:\n👉 [SPREADSHEET]({SHEET_LINK})\n"
        )

        message = "\n".join(msg_lines)

        await send_message(chat_id, message)
        return {"ok": True}

    # Handle other non commands messages from user
    elif text.lower() not in ["/faturas", f"/faturas@{BOT_USERNAME}", "/start", f"/start@{BOT_USERNAME}"]:
        await send_message(
            chat_id,
            "🥺 *Hmm... Desculpa!*\n\nSó consigo te ajudar por meio de comandos.\n\nTenta usar:\n👉 /faturas",
        )
        return {"ok": True}


# ======= Reminders for due-date notifications =======
def parse_due_day(value: str) -> int:
    """Extract the day-of-month from the sheet field (e.g., '25', 'Dia 25'). Clamps to 1..31."""
    digits = "".join(ch for ch in value if ch.isdigit())
    if not digits:
        return 1
    try:
        d = int(digits)
        return max(1, min(31, d))
    except Exception:
        return 1


def next_due_date_for_day(due_day: int, now: datetime) -> date:
    """Return the next calendar date for the given day-of-month from 'now', clamped to the month's last day."""
    def make_date(year: int, month: int, day_in: int) -> date:
        if month == 12:
            next_month_first = date(year + 1, 1, 1)
        else:
            next_month_first = date(year, month + 1, 1)
        last_day = (next_month_first - timedelta(days=1)).day
        return date(year, month, min(day_in, last_day))

    y, m = now.year, now.month
    cand = make_date(y, m, due_day)
    if cand >= now.date():
        return cand
    if m == 12:
        return make_date(y + 1, 1, due_day)
    return make_date(y, m + 1, due_day)


async def run_reminders(slot: str):
    """Send reminders for cards whose days until due are in REMINDER_DAYS, avoiding duplicates per (day, slot)."""
    now = datetime.now(TZ)
    rows = await fetch_csv_data()
    cards = [
        r
        for r in rows
        if r["CARTÃO"].strip().upper() in {"NUBANK", "INTER", "SANTANDER", "MERCPAGO"}
    ]
    emojis = {"NUBANK": "🟣", "INTER": "🟠", "SANTANDER": "🔴", "MERCPAGO": "⚪️"}

    for card in cards:
        name = card["CARTÃO"].strip().upper()
        status = card.get("SITUAÇÃO", "").strip().upper()
        due_str = card.get("D. VENC", "").strip()
        total = parse_brl_to_float(card.get("TOTAL", "").strip())
        if not due_str:
            continue
        due_day = parse_due_day(due_str)
        due_date = next_due_date_for_day(due_day, now)
        days_left = (due_date - now.date()).days
        if days_left not in REMINDER_DAYS:
            continue
        if status == "PAGA":
            continue
        key = f"{now.date().isoformat()}|{slot}|{name}|{days_left}"
        if key in sent_reminders:
            continue
        emoji = emojis.get(name, "💳")
        if days_left > 0:
            text = (
                f"{emoji} Lembrete: falta {days_left} dia para o vencimento do cartão {name.title()} (Dia {due_day}).\n"
                f"Valor: {format_currency(total)}\n"
                f"Situação: {status}\n"
            )
        else:
            text = (
                f"{emoji} Lembrete: hoje é o vencimento de {name.title()} (Dia {due_day}).\n"
                f"Valor: {format_currency(total)}\n"
                f"Situação: {status}\n"
            )
        for cid in AUTHORIZED_CHAT_IDS:
            await send_message(cid, text)
        sent_reminders.add(key)


def next_run_after(now: datetime):
    """Select the next scheduled run at REMINDER_HOURS today/tomorrow in TZ, returning (datetime, slot)."""
    candidates = []
    for day_offset in range(0, 2):
        base_date = (now + timedelta(days=day_offset)).date()
        for hr in REMINDER_HOURS:
            dt = datetime.combine(base_date, time(hr, 0), TZ)
            if dt >= now:
                candidates.append((dt, f"{hr:02d}"))
    candidates.sort(key=lambda x: x[0])
    if candidates:
        return candidates[0]
    return now + timedelta(hours=12), "00"


async def reminders_scheduler():
    """Wait until the next scheduled time and run reminders in a loop. Starts shortly after app startup."""
    await asyncio.sleep(10)
    while True:
        now = datetime.now(TZ)
        next_dt, slot = next_run_after(now)
        delay = (next_dt - now).total_seconds()
        if delay > 0:
            await asyncio.sleep(delay)
        try:
            await run_reminders(slot)
        except Exception as e:
            print(f"Erro no lembrete: {e}")

# ======= Self ping for trying to avoid Render sleeping =======
async def self_ping():
    await asyncio.sleep(10)  # Wait for app init
    url = f"{RENDER_URL}/ping"
    print("Iniciando ping interno para evitar sleep do Render...")

    while True:
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(url)
                print(f"Ping interno: status {r.status_code}")
        except Exception as e:
            print(f"Erro no ping interno: {e}")

        await asyncio.sleep(14 * 60)  # 14min


@app.on_event("startup")
async def startup_event():
    asyncio.create_task(self_ping())
    asyncio.create_task(reminders_scheduler())

    return {"ok": True}
