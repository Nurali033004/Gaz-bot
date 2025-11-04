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
import pytesseract
import pandas as pd
from dotenv import load_dotenv

# ===================== LOGGING =====================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ===================== .ENV =====================
load_dotenv("bot.env")
TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise ValueError("BOT_TOKEN topilmadi! bot.env faylni tekshiring.")

# ===================== GURUH VA ADMIN =====================
GROUP_CHAT_ID = "-1002672812101"
ADMIN_USER_IDS = [5019762222]

# ===================== SAQLASH =====================
DATA_FILE = "device_data.json"
device_data = {}

# ===================== MODEL MAP =====================
MODEL_MAP = {
    "1": "G1.6",
    "2": "G2.5",
    "4": "G4",
    "6": "G6",
    "7": "G10",
    "8": "G16",
}

# ===================== TOSHKENT SOATI (+5:00) =====================
TASHKENT_TZ = timezone(timedelta(hours=5))

def get_tashkent_time(dt):
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(TASHKENT_TZ).strftime("%d/%m/%Y %H:%M:%S")

# ===================== JSON =====================
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

# ===================== OCR =====================
async def extract_text_from_image(file_id, bot):
    try:
        file = await bot.get_file(file_id)
        image_bytes = await file.download_as_bytearray()
        image = Image.open(io.BytesIO(image_bytes))

        image = image.convert('L')
        enhancer = ImageEnhance.Contrast(image)
        image = enhancer.enhance(3.0)
        image = image.filter(ImageFilter.SHARPEN)
        image = image.point(lambda x: 0 if x < 130 else 255, '1')

        custom_config = r'--oem 3 --psm 6'
        text = pytesseract.image_to_string(image, lang='eng+rus', config=custom_config)
        text = re.sub(r'[^\w\s\.:-]', '', text)
        text = re.sub(r'\s+', ' ', text).strip()

        logger.info(f"OCR natijasi:\n{text}")
        return text
    except Exception as e:
        logger.error(f"Tesseract xatosi: {e}")
        return ""

# ===================== PARSING =====================
def parse_device_info(text):
    text = " " + text.upper() + " "
    logger.info(f"Parsing matni:\n{text}")

    seria = None
    for variant in [text, text.replace("O", "0").replace("o", "0")]:
        match = re.search(r"TPGR0[0-9A-Z]{10}", variant)
        if match:
            start = text.find("TPGR")
            if start != -1:
                seria = text[start:start+16]
                break
    if not seria:
        logger.warning("TPGR topilmadi")
        return None

    model_digit = seria[6]
    model = MODEL_MAP.get(model_digit, "Noma'lum")

    metro = re.search(r"0217", text)
    metro = metro.group(0) if metro else "Noma'lum"

    non_metro = re.search(r"0575", text)
    non_metro = non_metro.group(0) if non_metro else "Noma'lum"

    logger.info(f"Natija → {seria} | {model} | {metro} | {non_metro}")
    return {
        "seriya": seria,
        "model": model,
        "metrological": metro,
        "non_metrological": non_metro,
    }

# ===================== GURUH TINGLOVCHI =====================
async def listen_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.message.chat_id) != GROUP_CHAT_ID:
        return
    if not update.message.photo:
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
            f"Yangi qurilma!\n"
            f"Seriya: `{seria}`\n"
            f"Model: `{info['model']}`\n"
            f"Metro: `{info['metrological']}`\n"
            f"Non: `{info['non_metrological']}`\n"
            f"Vaqt: `{message_time}`",
            parse_mode="Markdown"
        )
        logger.info(f"Yangi: {seria} | {message_time}")
    else:
        await update.message.reply_text("Bu qurilma allaqachon saqlangan.")

# ===================== /report =====================
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

# ===================== /start =====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Bot ishlayapti!\n"
        "Guruhga rasm tashlang — bot avtomatik o‘qiydi.\n"
        "/report — Excel hisobotini olish.\n"
        "Vaqt: Toshkent (+5:00)"
    )

# ===================== XATO =====================
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Xato: {context.error}")

# ===================== MAIN =====================
def main():
    load_data()
    request = HTTPXRequest(connect_timeout=30, read_timeout=30)
    app = Application.builder().token(TOKEN).request(request).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("report", generate_report))
    app.add_handler(MessageHandler(filters.Chat(int(GROUP_CHAT_ID)) & filters.PHOTO, listen_group))
    app.add_error_handler(error_handler)
    print("Bot ishga tushdi... (Toshkent vaqti: +5:00)")
    app.run_polling()

if __name__ == "__main__":
    main()
