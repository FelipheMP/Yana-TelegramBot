import os
import csv
import httpx
from io import StringIO
from fastapi import FastAPI
from pydantic import BaseModel
from dotenv import load_dotenv
from typing import Optional

load_dotenv()

app = FastAPI()

BOT_TOKEN = os.getenv("BOT_TOKEN")
CSV_URL = os.getenv("CSV_URL")

if not BOT_TOKEN or not CSV_URL:
    raise RuntimeError("Set BOT_TOKEN and CSV_URL in .env file")

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

user_states = {}

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
    data = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown"
    }
    if reply_markup:
        data["reply_markup"] = reply_markup
    async with httpx.AsyncClient() as client:
        await client.post(f"{TELEGRAM_API}/sendMessage", json=data)

@app.post(f"/webhook/{BOT_TOKEN}")
async def telegram_webhook(update: TelegramUpdate):
    if not update.message:
        return {"ok": True}

    chat_id = update.message["chat"]["id"]
    text = update.message.get("text", "").strip()
    user_id = update.message["from"]["id"]

    if text == "/faturas":
        rows = await fetch_csv_data()

        # Filter cards data
        cards = [r for r in rows if r["CARTÃO"].strip().upper() in {"NUBANK", "INTER", "SANTANDER"}]

        # Find summary rows
        total_final = next((r for r in rows if r["CARTÃO"].strip().upper() == "TOTAL FINAL"), None)
        a_pagar = next((r for r in rows if r["CARTÃO"].strip().upper() == "A PAGAR"), None)

        if not cards:
            await send_message(chat_id, "Não foi possível encontrar os dados dos cartões na planilha.")
            return {"ok": True}

        # Format message
        msg_lines = []
        for card in cards:
            total = parse_brl_to_float(card["TOTAL"])
            msg_lines.append(f"*{card['CARTÃO'].strip()}*: {format_currency(total)}")

        # Add summaries if found
        if total_final:
            total = parse_brl_to_float(total_final["TOTAL"])
            msg_lines.append(f"\n*TOTAL FINAL*: {format_currency(total)}")
        if a_pagar:
            total = parse_brl_to_float(a_pagar["TOTAL"])
            msg_lines.append(f"*A PAGAR*: {format_currency(total)}")

        # Add status and vencimento info
        # Assuming vencimento (due date) is same per card from "D. VENC"
        msg_lines.append("\n*STATUS E VENCIMENTO:*")
        for card in cards:
            venc = card["D. VENC"].strip()
            status = card["SITUAÇÃO"].strip()
            msg_lines.append(f"{card['CARTÃO'].strip()}\n- Vencimento: Dia {venc}\n- Situação: {status}")

        # Assuming all cards have the same month in "MÊS" field
        month = cards[0]["MÊS"].strip()
        msg_lines.append(f"\n*MÊS:* {month}")

        message = "\n".join(msg_lines)

        await send_message(chat_id, message)
        return {"ok": True}

    return {"ok": True}
