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
        if not rows or len(rows) < 2:
            await send_message(chat_id, "Não foi possível recuperar os dados da fatura.")
            return {"ok": True}

        header = [h.strip().upper() for h in rows[0]]
        # Índices conforme colunas
        try:
            card_idx = header.index("CARTÃO")
            month_idx = header.index("MÊS")
            due_idx = header.index("D.VENC")
            total_idx = header.index("TOTAL")
            status_idx = header.index("SITUAÇÃO")
        except ValueError:
            await send_message(chat_id, "Formato da planilha inválido.")
            return {"ok": True}

        # Vamos pegar o mês atual (ou o primeiro mês encontrado na planilha)
        # Se preferir, pode mudar para uma string fixa, ex: "05-2025"
        month_to_show = None
        for row in rows[1:]:
            if row and row[month_idx].strip():
                month_to_show = row[month_idx].strip()
                break

        if not month_to_show:
            await send_message(chat_id, "Nenhum mês válido encontrado na planilha.")
            return {"ok": True}

        # Filtra linhas do mês escolhido
        filtered_rows = [row for row in rows[1:] if row and row[month_idx].strip() == month_to_show]

        # Dicionário para guardar dados por cartão
        data_by_card = {}
        for row in filtered_rows:
            card = row[card_idx].strip()
            due = row[due_idx].strip()
            total = row[total_idx].strip()
            status = row[status_idx].strip()
            data_by_card[card] = {
                "due": due,
                "total": total,
                "status": status
            }

        # Monta a mensagem formatada
        message_lines = [
            "*Resumo das Faturas*",
            f"MÊS: *{month_to_show}*\n"
        ]

        # Lista fixa de cartões que você mencionou
        cards_list = ["NUBANK", "INTER", "SANTANDER"]

        total_final = 0.0
        pagar = 0.0  # Supondo que seja o total a pagar, pode ser ajustado depois

        for card in cards_list:
            if card in data_by_card:
                try:
                    total_value = float(data_by_card[card]["total"].replace(",", "."))
                except:
                    total_value = 0.0
                total_final += total_value
                message_lines.append(f"{card}: R$ {data_by_card[card]['total']}")

        message_lines.append(f"\nTOTAL FINAL: R$ {total_final:.2f}")

        # Supondo que 'A PAGAR' seja o mesmo que 'TOTAL FINAL' menos algo, ou se tiver na planilha, ajuste aqui
        message_lines.append(f"A PAGAR: R$ {total_final:.2f}\n")

        # Status pode variar entre cartões, então mostra status de cada um
        for card in cards_list:
            if card in data_by_card:
                message_lines.append(f"STATUS {card}: {data_by_card[card]['status']}")

        message_lines.append("\n*Dia de Vencimento*")
        for card in cards_list:
            if card in data_by_card:
                message_lines.append(f"{card} - {data_by_card[card]['due']}")

        message_text = "\n".join(message_lines)

        await send_message(chat_id, message_text)
        return {"ok": True}

    return {"ok": True}
