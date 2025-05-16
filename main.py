import os
import csv
import httpx
from io import StringIO
from fastapi import FastAPI, Request
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

# Armazena meses disponíveis
cached_months = []
user_states = {}

class TelegramUpdate(BaseModel):
    update_id: int
    message: Optional[dict] = None

async def fetch_csv_data():
    async with httpx.AsyncClient() as client:
        response = await client.get(CSV_URL)
        response.raise_for_status()
        return list(csv.reader(StringIO(response.text)))

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

    if user_states.get(user_id) == "waiting_for_month":
        month = text
        if month not in cached_months:
            await send_message(chat_id, "Mês inválido. Por favor escolha um mês da lista.")
            return {"ok": True}

        rows = await fetch_csv_data()
        header = rows[0]
        for row in rows[1:]:
            if row and row[0].strip() == month:
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

        await send_message(chat_id, "Dados do mês não encontrados.")
        return {"ok": True}

    if text == "/faturas":
        rows = await fetch_csv_data()
        global cached_months
        months = [row[0] for row in rows[1:] if row]
        cached_months = months

        months_list = "\n".join(months)
        await send_message(
            chat_id,
            "Escolha o mês da fatura enviando o nome exatamente como na lista:\n\n" + months_list,
        )
        user_states[user_id] = "waiting_for_month"
        return {"ok": True}

    return {"ok": True}
