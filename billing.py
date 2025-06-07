# Billing bot for Google Cloud Run
import os
import io
import logging
import requests
from datetime import datetime
from flask import Flask, request
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

logging.basicConfig(level=logging.INFO)

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/spreadsheets",
]


def build_services():
    credentials = service_account.Credentials.from_service_account_file(
        os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"), scopes=SCOPES
    )
    drive = build("drive", "v3", credentials=credentials)
    docs = build("docs", "v1", credentials=credentials)
    sheets = build("sheets", "v4", credentials=credentials)
    return drive, docs, sheets


def send_telegram_message(token: str, chat_id: str, text: str):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    requests.post(url, json={"chat_id": chat_id, "text": text})


class BillingBot:
    def __init__(self):
        self.token = os.environ.get("TELEGRAM_TOKEN")
        self.bot_chat_id = os.environ.get("BOT_CHAT_ID")
        self.invoice_template = os.environ.get("INVOICE_TEMPLATE_ID")
        self.act_template = os.environ.get("ACT_TEMPLATE_ID")
        self.pdf_folder = os.environ.get("PDF_FOLDER_ID")
        self.spreadsheet = os.environ.get("SPREADSHEET_ID")
        self.drive, self.docs, self.sheets = build_services()
        self.generate_bill = "generate_bill"
        self.bills_done = "bills_done"
        self.tgsheet = "telegram"

    # --- Spreadsheet helpers
    def append_row(self, sheet_name: str, values):
        body = {"values": [values]}
        self.sheets.spreadsheets().values().append(
            spreadsheetId=self.spreadsheet,
            range=sheet_name,
            valueInputOption="USER_ENTERED",
            body=body,
        ).execute()

    def get_values(self, range_):
        result = (
            self.sheets.spreadsheets()
            .values()
            .get(spreadsheetId=self.spreadsheet, range=range_)
            .execute()
        )
        return result.get("values", [])

    # --- Invoice logic
    def invoice_number_check(self, number: int) -> int:
        numbers = self.get_values(f"{self.bills_done}!C3:C")
        exist = {int(n[0]) for n in numbers if n}
        while number in exist:
            number += 1
        return number

    def create_pdf(self, row):
        invoice_copy = (
            self.drive.files()
            .copy(fileId=self.invoice_template, body={"parents": [self.pdf_folder]})
            .execute()
        )
        act_copy = (
            self.drive.files()
            .copy(fileId=self.act_template, body={"parents": [self.pdf_folder]})
            .execute()
        )
        invoice_id = invoice_copy["id"]
        act_id = act_copy["id"]

        invoice_num = self.invoice_number_check(int(row[0]))
        replacements = {
            "{invoice_number}": str(invoice_num),
            "{invoice_date}": row[1],
            "{client_name}": row[3],
            "{client_edrpou}": row[4],
            "{client_address}": row[5],
            "{service_name}": row[6],
            "{service_count}": row[7],
            "{service_amount}": row[8],
            "{service_amount_words}": row[9],
        }
        self._replace_text(invoice_id, replacements)
        self._replace_text(act_id, replacements)

        invoice_pdf = (
            self.drive.files()
            .export(fileId=invoice_id, mimeType="application/pdf")
            .execute()
        )
        invoice_file = (
            self.drive.files()
            .create(
                body={"name": f"invoice_{invoice_num}_{row[3]}", "parents": [self.pdf_folder]},
                media_body=MediaIoBaseUpload(io.BytesIO(invoice_pdf), mimetype="application/pdf"),
            )
            .execute()
        )
        act_pdf = (
            self.drive.files()
            .export(fileId=act_id, mimeType="application/pdf")
            .execute()
        )
        act_file = (
            self.drive.files()
            .create(
                body={"name": f"act_{invoice_num}_{row[3]}", "parents": [self.pdf_folder]},
                media_body=MediaIoBaseUpload(io.BytesIO(act_pdf), mimetype="application/pdf"),
            )
            .execute()
        )
        # Clean up temporary docs
        self.drive.files().delete(fileId=invoice_id).execute()
        self.drive.files().delete(fileId=act_id).execute()

        invoice_url = f"https://drive.google.com/uc?id={invoice_file['id']}"
        act_url = f"https://drive.google.com/uc?id={act_file['id']}"

        count = len(self.get_values(f"{self.bills_done}!A2:A")) + 1
        today = datetime.utcnow().strftime("%Y-%m-%d")
        row_data = [
            count,
            today,
            invoice_num,
            row[1],
            row[2],
            row[3],
            row[4],
            row[5],
            row[6],
            row[7],
            row[8],
            row[9],
            invoice_url,
            act_url,
            row[10],
            "no",
        ]
        self.append_row(self.bills_done, row_data)
        tel_msg = f"{row[1]}\n{row[3]}\nРахунок: {invoice_url}\nАкт: {act_url}"
        send_telegram_message(self.token, self.bot_chat_id, tel_msg)

    def _replace_text(self, doc_id, replacements):
        requests_body = [
            {
                "replaceAllText": {
                    "containsText": {"text": key, "matchCase": True},
                    "replaceText": value,
                }
            }
            for key, value in replacements.items()
        ]
        self.docs.documents().batchUpdate(documentId=doc_id, body={"requests": requests_body}).execute()

    def create_bill(self):
        data = self.get_values(f"{self.generate_bill}!B3:L3")
        for row in data:
            try:
                self.create_pdf(row)
            except Exception as e:
                logging.error(f"Failed to create bill: {e}")

    def handle_update(self, update: dict):
        message = update.get("message", {})
        if "text" not in message:
            return
        text = message["text"]
        chat_id = message["chat"]["id"]
        parts = text.split()
        if len(parts) == 3:
            self.append_row(self.tgsheet, parts)
            send_telegram_message(self.token, chat_id, f"Дані успішно додані: {', '.join(parts)}")
        elif parts[0] == "/bill":
            self.create_bill()
            send_telegram_message(self.token, chat_id, "Рахунок згенеровано")
        else:
            send_telegram_message(self.token, chat_id, "Помилка: введіть три поля через пробіл.")


bot = BillingBot()
app = Flask(__name__)


@app.route("/webhook", methods=["POST"])
def webhook():
    bot.handle_update(request.get_json(force=True))
    return "OK"


@app.route("/health", methods=["GET"])
def health():
    return "OK"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
