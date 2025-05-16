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
CSV_URL = os.getenv("CSV_URL")  # URL of the CSV file representing the SUMMARY tab

if not BOT_TOKEN or not CSV_URL:
    raise RuntimeError("Set BOT_TOKEN and CSV_URL in the .env file")

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

class TelegramUpdate(BaseModel):
    update_id: int
    message: Optional[dict] = None

async def fetch_csv():
    async with httpx.AsyncClient(follow_redirects=True) as client:
        response = await client.get(CSV_URL)
        response.raise_for_status()
        return list(csv.reader(StringIO(response.text)))

async def send_message(chat_id: int, text: str):
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown"
    }
    async with httpx.AsyncClient() as client:
        await client.post(f"{TELEGRAM_API}/sendMessage", json=payload)

@app.post(f"/webhook/{BOT_TOKEN}")
async def telegram_webhook(update: TelegramUpdate):
    if not update.message:
        return {"ok": True}

    chat_id = update.message["chat"]["id"]
    text = update.message.get("text", "").strip()

    if text == "/faturas":
        rows = await fetch_csv()
        if not rows:
            await send_message(chat_id, "Não foi possível recuperar os dados da fatura.")
            return {"ok": True}

        # Format the message by joining CSV rows, limiting max lines to avoid flooding Telegram
        message = "*Resumo das Faturas:*\n\n"
        max_lines = 20  # Ajuste esse limite conforme necessário
        for i, row in enumerate(rows):
            formatted_line = " | ".join(row)
            message += formatted_line + "\n"
            if i >= max_lines:
                message += "\n_... mais linhas não exibidas_\n"
                break

        await send_message(chat_id, message)
        return {"ok": True}

    return {"ok": True}
