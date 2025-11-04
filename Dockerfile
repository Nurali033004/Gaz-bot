FROM python:3.11-slim

# Tesseract o'rnatish
RUN apt-get update && apt-get install -y tesseract-ocr libtesseract-dev && rm -rf /var/lib/apt/lists/*

# Ish papkasi
WORKDIR /app

# Fayllarni ko'chirish
COPY . .

# Paketlar
RUN pip install --no-cache-dir -r requirements.txt

# Botni ishga tushirish
CMD ["python", "main.py"]
