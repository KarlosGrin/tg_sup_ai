# ═══════════════════════════════════════════════════════════════
# Docker-образ для самого Telegram-бота (опционально)
# Бота можно запускать и напрямую: python main.py
# ═══════════════════════════════════════════════════════════════
FROM python:3.12-slim

WORKDIR /app

# Устанавливаем зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем исходный код
COPY . .

# Директории для файлов (создаются при старте)
RUN mkdir -p downloads processed

# Токен бота передаётся через .env или переменную окружения
ENV BOT_TOKEN=""

CMD ["python", "main.py"]
