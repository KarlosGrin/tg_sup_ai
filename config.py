"""
Конфигурация бота.
Загружает настройки из переменных окружения (.env файла).
"""

import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # Telegram
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
    CHANNEL_ID: str = os.getenv("CHANNEL_ID", "-1004469769190")  # По умолчанию указанный канал
    ADMIN_IDS: list[int] = list(map(int, os.getenv("ADMIN_IDS", "").split(","))) if os.getenv("ADMIN_IDS") else []

    # OpenAI
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o")
    OPENAI_MAX_TOKENS: int = int(os.getenv("OPENAI_MAX_TOKENS", "4096"))
    OPENAI_TEMPERATURE: float = float(os.getenv("OPENAI_TEMPERATURE", "0.3"))

    # Прокси для Telegram API (SOCKS5 или HTTP)
    PROXY_URL: str = os.getenv("PROXY_URL", "")
    PROXY_ENABLED: bool = os.getenv("PROXY_ENABLED", "false").lower() == "true"

    # Выбор AI провайдера: "openai" (по умолчанию) или "gemini"
    AI_PROVIDER: str = os.getenv("AI_PROVIDER", "openai").lower()

    # Режим выполнения кода: "local" (локальный exec) или "assistants" (песочница Gemini)
    EXECUTION_MODE: str = os.getenv("EXECUTION_MODE", "local").lower()

    # Gemini
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-3-flash-preview")

    # Пути
    DOWNLOAD_DIR: str = os.getenv("DOWNLOAD_DIR", "downloads")
    PROCESSED_DIR: str = os.getenv("PROCESSED_DIR", "processed")
    MAX_FILE_SIZE_MB: int = int(os.getenv("MAX_FILE_SIZE_MB", "20"))

    # Rate limiting
    RATE_LIMIT_REQUESTS_PER_MIN: int = int(os.getenv("RATE_LIMIT_REQUESTS_PER_MIN", "5"))
    RATE_LIMIT_FILE_UPLOADS_PER_HOUR: int = int(os.getenv("RATE_LIMIT_FILE_UPLOADS_PER_HOUR", "20"))


config = Config()
