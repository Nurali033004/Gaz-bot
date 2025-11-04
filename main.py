import logging
import re
import io
import json
import os
from datetime import datetime, timedelta, timezone
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
from telegram.request import HTTPXRequest
from PIL import Image, ImageEnhance, ImageFilter
import easyocr
import numpy as np
import pandas as pd
from dotenv import load_dotenv
from flask import Flask, request
import threading
import asyncio

# ------------------ LOG SOZLAMASI ------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ------------------ ENV O‘QISH ------------------
load_dotenv("bot.env")
TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise ValueError("BOT_TOKEN topilmadi!")

GROUP_CHAT_ID = "-1002672812101"
ADMIN_USER_IDS = [5721263149, 5019762222]

DATA_FILE = "device_data.json"
device_data = {}

MODEL_MAP = {
    "1": "G1.6",
    "2": "G2.5",
    "4": "G4",
    "6": "G6",
    "7": "G10",
    "8": "G16",
}

TASHKENT_TZ = timezone(timedelta(hours=5))

# ------------------ EASYOCR ------------------
reader = easyocr.Reader(['en', 'ru'], gpu=False)

# ------------------ FLASK WEBHOOK ------------------
flask_app = Flask(__name__)

@flask_app.route('/webhook', methods=['POST'])
def webhook_handler():
    if request.method == 'POST':
        update = Update.de_json(request.get_json(force=True), application.bot)
        application.process_update(update)
    return 'OK', 200

# ------------------ FUNKSIYALAR ------------------
def get_tashkent_time(dt):
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(TASHKENT_TZ).strftime("%d/%m/%Y %H:%M:%S")

def save_data():
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(device_data, f, ensure_ascii=False, indent=4)
        logger.info("Ma'lumotlar saqlandi.")
    except Exception as e:
        logger.error(f"Saqlash xatosi: {e}")

def load_data():
    global device_data
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                device_data = json.load(f)
            logger.info(f"{len(device_data)} ta ma'lumot yuklandi.")
        except Exception as e:
            logger.error(f"Yuklash xatosi: {e}")
            device_data = {}
    else:
        save_data()

async def extract_text_from_image(file_id, bot):
    try:
        file = await bot.get_file(file_id)
        image_bytes = await file.download_as_bytearray()
        image = Image.open(io.BytesIO(image_bytes))
        image = image.convert('L')
        enhancer = ImageEnhance.Contrast(image)
        image = enhancer.enhance(2.0)
        image = image.filter(ImageFilter.SHARPEN)
        results = reader.readtext(np.array(image), detail=0)
        text = " ".join(results)
        text = re.sub(r'[^\w\s\.:-]', '', text)
        text = re.sub(r'\s+', ' ', text).strip()
        logger.info(f"O‘qilgan matn: {text}")
        return text
    except Exception as e:
        logger.error(f"OCR xatosi: {e}")
        return ""

def parse_device_info(text):
    text = " " + text.upper() + " "
    for variant in [text, text.replace("O", "0").replace("o", "0")]:
        match = re.search(r"TPGR0[0-9A-Z]{10}", variant)
        if match:
            start = text.find("TPGR")
            if start != -1:
                seria = text[start:start + 16]
                model_digit = seria[6]
                model = MODEL_MAP.get(model_digit, "Noma'lum")
                metro = "0217" if "0217" in text else "Noma'lum"
                non_metro = "0575" if "0575" in text else "Noma'lum"
                return {
                    "seriya": seria,
                    "model": model,
                    "metrological": metro,
                    "non_metrological": non_metro,
                }
    return None

# ------------------ HANDLERLAR ------------------
async def listen_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.message.chat_id) != GROUP_CHAT_ID or not update.message.photo:
        return
    file_id = update.message.photo[-1].file_id
    message_time = get_tashkent_time(update.message.date)
    text = await extract_text_from_image(file_id, context.bot)
    if not text:
        await update.message.reply_text("Rasm o‘qilmadi.")
        return
    info = parse_device_info(text)
    if not info:
        await update.message.reply_text("Ma'lumot topilmadi.")
        return
    seria = info["seriya"]
    if seria not in device_data:
        device_data[seria] = {
            "model": info["model"],
            "metrological": info["metrological"],
            "non_metrological": info["non_metrological"],
            "timestamp": message_time,
        }
        save_data()
        await update.message.reply_text(
            f"Yanggi qurilma!\n"
            f"Seriya: `{seria}`\n"
            f"Model: `{info['model']}`\n"
            f"Metro: `{info['metrological']}`\n"
            f"Non: `{info['non_metrological']}`\n"
            f"Vaqt: `{message_time}`",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text("Bu qurilma allaqachon saqlangan.")

async def generate_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id not in ADMIN_USER_IDS:
        await update.message.reply_text("Sizda huquq yo‘q!")
        return
    if not device_data:
        await update.message.reply_text("Ma'lumot yo‘q.")
        return
    rows = []
    for seria, data in device_data.items():
        rows.append({
            "Seriya raqam": seria,
            "Model": data["model"],
            "Metrological firmware": data["metrological"],
            "Non Metrological firmware": data["non_metrological"],
            "Tashlangan sana va vaqt": data["timestamp"],
        })
    df = pd.DataFrame(rows)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, sheet_name="Qurilmalar", index=False)
        worksheet = writer.sheets["Qurilmalar"]
        for i, col in enumerate(df.columns):
            max_len = max(df[col].astype(str).str.len().max(), len(col)) + 2
            worksheet.set_column(i, i, max_len)
    output.seek(0)
    filename = f"qurilmalar_{datetime.now(TASHKENT_TZ).strftime('%Y%m%d_%H%M')}.xlsx"
    await update.message.reply_document(
        document=output,
        filename=filename,
        caption=f"Jami: {len(device_data)} ta qurilma | Toshkent vaqti"
    )

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Bot ishlayapti!\n"
        "Guruhga rasm tashlang — avtomatik o‘qiydi.\n"
        "/report — Excel hisobot.\n"
        "Vaqt: Toshkent (+5:00)"
    )

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Xato: {context.error}")

# ------------------ ASOSIY ISHGA TUSHIRISH ------------------
application = None

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    flask_app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

async def set_webhook():
    webhook_url = f"https://{os.environ.get('RENDER_EXTERNAL_HOSTNAME')}.onrender.com/webhook"
    await application.bot.set_webhook(url=webhook_url)
    logger.info(f"Webhook o'rnatildi: {webhook_url}")

def main():
    global application
    load_data()
    request = HTTPXRequest(connect_timeout=60, read_timeout=60)
    application = Application.builder().token(TOKEN).request(request).build()

    # Handlerlar
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("report", generate_report))
    application.add_handler(MessageHandler(filters.Chat(int(GROUP_CHAT_ID)) & filters.PHOTO, listen_group))
    application.add_error_handler(error_handler)

    # Flask serverni alohida thread'da ishga tushirish
    threading.Thread(target=run_flask, daemon=True).start()

    # Webhook o'rnatish
    asyncio.run(set_webhook())

    logger.info("Bot ishlayapti (Webhook rejimida)...")
    # run_polling() yo'q! Faqat webhook
    application.run_polling()  # Bu faqat dastlabki test uchun, keyin o'chiriladi

if __name__ == "__main__":
    main()
