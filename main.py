import os
from fastapi import FastAPI
from pydantic import BaseModel
import httpx
from typing import Optional, Dict, Any
import json

app = FastAPI()

BOT_TOKEN = os.getenv("BOT_TOKEN")
SHEET_ID = os.getenv("SHEET_ID")

if not BOT_TOKEN or not SHEET_ID:
    raise RuntimeError("Set BOT_TOKEN and SHEET_ID in environment variables")

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
GOOGLE_SHEETS_API = f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values"

# Estado do usuário e meses disponíveis por ID
user_states: Dict[int, str] = {}
user_months: Dict[int, list] = {}

class TelegramMessage(BaseModel):
    message_id: int
    text: Optional[str] = None
    chat: Dict[str, Any]
    from_: Dict[str, Any]

    class Config:
        fields = {'from_': 'from'}  # map 'from' JSON key to 'from_' attribute

class TelegramUpdate(BaseModel):
    update_id: int
    message: Optional[TelegramMessage] = None

async def get_sheet_values(range_: str):
    url = f"{GOOGLE_SHEETS_API}/{range_}"
    async with httpx.AsyncClient() as client:
        try:
            r = await client.get(url)
            r.raise_for_status()
            return r.json()
        except httpx.HTTPError as e:
            print(f"Erro ao buscar valores da planilha: {e}")
            return {}

async def send_message(chat_id: int, text: str, reply_markup=None):
    data = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
    }
    if reply_markup:
        data["reply_markup"] = json.dumps(reply_markup)
    async with httpx.AsyncClient() as client:
        await client.post(f"{TELEGRAM_API}/sendMessage", json=data)

@app.post(f"/webhook/{BOT_TOKEN}")
async def telegram_webhook(update: TelegramUpdate):
    if not update.message:
        return {"ok": True}

    chat_id = update.message.chat["id"]
    user_id = update.message.from_["id"]
    text = update.message.text or ""

    # Usuário está selecionando o mês
    if user_states.get(user_id) == "waiting_for_month":
        month = text.strip()
        valid_months = user_months.get(user_id, [])

        if month.lower() not in [m.lower() for m in valid_months]:
            await send_message(chat_id, "Mês inválido. Por favor, escolha um mês da lista enviada.")
            return {"ok": True}

        # Buscar dados da planilha
        sheet_data = await get_sheet_values("A2:F1000")
        row = None
        for r in sheet_data.get("values", []):
            if r and r[0].lower() == month.lower():
                row = r
                break

        if not row:
            await send_message(chat_id, "Dados do mês não encontrados.")
            return {"ok": True}

        row += [""] * (6 - len(row))  # garantir que tenha 6 colunas

        msg = (
            f"*Mês:* {row[0]}\n"
            f"NUBANK: {row[1]}\n"
            f"SANTANDER: {row[2]}\n"
            f"INTER: {row[3]}\n"
            f"TOTAL: {row[4]}\n\n"
            f"*Status:* {row[5]}"
        )

        await send_message(chat_id, msg)
        user_states.pop(user_id, None)
        user_months.pop(user_id, None)
        return {"ok": True}

    # Comando /faturas
    if text.strip() == "/faturas":
        sheet_data = await get_sheet_values("A2:A1000")
        months = [row[0] for row in sheet_data.get("values", []) if row]

        if not months:
            await send_message(chat_id, "Nenhuma fatura encontrada.")
            return {"ok": True}

        # Armazenar lista por usuário
        user_months[user_id] = months
        user_states[user_id] = "waiting_for_month"

        keyboard = {
            "keyboard": [[{"text": m}] for m in months],
            "one_time_keyboard": True,
            "resize_keyboard": True
        }

        await send_message(
            chat_id,
            "Escolha o mês da fatura tocando em um dos botões ou digitando exatamente como mostrado:",
            reply_markup=keyboard
        )
        return {"ok": True}

    return {"ok": True}
