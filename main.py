import os
from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel
import httpx
from typing import Optional

app = FastAPI()

BOT_TOKEN = os.getenv("BOT_TOKEN")
SHEET_ID = os.getenv("SHEET_ID")

if not BOT_TOKEN or not SHEET_ID:
    raise RuntimeError("Set BOT_TOKEN and SHEET_ID in environment variables")

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

GOOGLE_SHEETS_API = f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values"

# Public sheet, no API key needed

# Cache months (assuming first column is 'Mês')
cached_months = []

# For tracking user states (very basic)
user_states = {}

class TelegramUpdate(BaseModel):
    update_id: int
    message: Optional[dict] = None

async def get_sheet_values(range_: str):
    url = f"{GOOGLE_SHEETS_API}/{range_}"
    async with httpx.AsyncClient() as client:
        r = await client.get(url)
        r.raise_for_status()
        return r.json()

async def send_message(chat_id: int, text: str, reply_markup=None):
    data = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    if reply_markup:
        data["reply_markup"] = reply_markup
    async with httpx.AsyncClient() as client:
        await client.post(f"{TELEGRAM_API}/sendMessage", json=data)

@app.post(f"/webhook/{BOT_TOKEN}")
async def telegram_webhook(update: TelegramUpdate):
    if not update.message:
        return {"ok": True}  # Ignore non-message updates

    chat_id = update.message["chat"]["id"]
    text = update.message.get("text", "")

    user_id = update.message["from"]["id"]

    # Check user state for month selection
    if user_states.get(user_id) == "waiting_for_month":
        month = text.strip()
        # Validate month against cached months
        if month not in cached_months:
            await send_message(chat_id, f"Mês inválido. Por favor escolha um mês da lista.")
            return {"ok": True}

        # Fetch the row for this month (assume sheet structure below)
        # Columns: Mês, NUBANK, SANTANDER, INTER, TOTAL, Status
        sheet_data = await get_sheet_values("A2:F1000")  # assuming max 1000 rows

        # Find matching row
        row = None
        for r in sheet_data.get("values", []):
            if r[0] == month:
                row = r
                break

        if not row:
            await send_message(chat_id, "Dados do mês não encontrados.")
            return {"ok": True}

        # Fill missing columns with empty string if needed
        row += [""] * (6 - len(row))

        message = (
            f"*Mês:* {row[0]}\n"
            f"NUBANK: {row[1]}\n"
            f"SANTANDER: {row[2]}\n"
            f"INTER: {row[3]}\n"
            f"TOTAL: {row[4]}\n\n"
            f"*Status:* {row[5]}"
        )

        await send_message(chat_id, message)
        user_states.pop(user_id, None)
        return {"ok": True}

    # If command /faturas
    if text == "/faturas":
        # Get months from sheet
        sheet_data = await get_sheet_values("A2:A1000")
        months = [row[0] for row in sheet_data.get("values", []) if row]
        if not months:
            await send_message(chat_id, "Nenhuma fatura encontrada.")
            return {"ok": True}

        global cached_months
        cached_months = months

        months_list = "\n".join(months)
        await send_message(
            chat_id,
            "Escolha o mês da fatura enviando o nome exatamente como na lista:\n\n" + months_list,
        )
        user_states[user_id] = "waiting_for_month"
        return {"ok": True}

    return {"ok": True}
