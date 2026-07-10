"""
Главный файл Telegram бота — AI-ассистент для работы с Excel/Word файлами.

Запуск:
    python main.py

Переменные окружения (файл .env):
    BOT_TOKEN          — токен Telegram бота (обязательно)
    CHANNEL_ID         — ID канала (по умолчанию -1004469769190)
    OPENAI_API_KEY     — ключ OpenAI API (обязательно)
    OPENAI_MODEL       — модель (по умолчанию gpt-4o)
    EXECUTION_MODE     — "local" (по умолчанию) или "assistants"
    ADMIN_IDS          — ID администраторов через запятую
"""

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode

from config import config
from handlers.common import _cleanup_stale_entries
from handlers.commands import router as commands_router
from handlers.admin import router as admin_router
from handlers.documents import router as documents_router
from handlers.callbacks import router as callbacks_router
from handlers.text import router as text_router
from services.file_service import file_service
from services.profiler import ProfilingMiddleware
from utils.profiler_decorator import start_yappi

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("bot.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

# placeholder для yappi stop (заполняется в on_startup)
_stop_yappi = lambda: None


async def on_startup(bot: Bot):
    """Действия при запуске бота."""
    logger.info("=" * 50)
    logger.info("Бот запускается...")

    # Проверка конфигурации
    if not config.BOT_TOKEN:
        logger.error("BOT_TOKEN не задан! Укажите его в .env файле.")
        raise ValueError("BOT_TOKEN is required")

    if config.AI_PROVIDER == "openai" and not config.OPENAI_API_KEY:
        logger.error("OPENAI_API_KEY не задан! Укажите его в .env файле.")
        raise ValueError("OPENAI_API_KEY is required")

    if config.AI_PROVIDER == "gemini" and not config.GEMINI_API_KEY:
        logger.error("GEMINI_API_KEY не задан! Укажите его в .env файле.")
        raise ValueError("GEMINI_API_KEY is required")

    # Запуск yappi-профайлера для async-кода (если ENABLE_PROFILING=true)
    global _stop_yappi
    if config.ENABLE_PROFILING:
        _stop_yappi = start_yappi()
        logger.info("📊 Async-профилирование ВКЛЮЧЕНО (yappi)")
    else:
        _stop_yappi = lambda: None

    # Предупреждение о режиме песочницы
    if not config.DOCKER_ENABLED:
        logger.warning("⚠️" + "=" * 60)
        logger.warning("⚠️ DOCKER_ENABLED=false — код выполняется в прямом subprocess!")
        logger.warning("⚠️ Без изоляции ОС (--network none, --read-only, --cap-drop ALL).")
        logger.warning("⚠️ Рекомендуется включить DOCKER_ENABLED=true для продакшена.")
        logger.warning("⚠️ Установите Docker Desktop и выполните: docker compose build sandbox")
        logger.warning("⚠️" + "=" * 60)
    else:
        logger.info("🐳 Docker-изоляция песочницы ВКЛЮЧЕНА")

    # Очистка старых временных файлов при запуске
    file_service.cleanup_old_files(max_age_hours=1)

    # Принудительно удаляем вебхук, чтобы избежать конфликтов
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("🌐 Вебхук удалён (если был установлен)")
    except Exception as e:
        logger.warning(f"⚠️ Не удалось удалить вебхук: {e}")

    # Получаем информацию о боте
    bot_info = await bot.get_me()
    logger.info(f"Бот: @{bot_info.username} (ID: {bot_info.id})")

    # Проверяем права в канале
    try:
        chat = await bot.get_chat(config.CHANNEL_ID)
        logger.info(f"Канал: {chat.title} (ID: {chat.id})")
    except Exception as e:
        logger.warning(f"Не удалось получить информацию о канале {config.CHANNEL_ID}: {e}")
        logger.info("Бот будет работать в режиме личных сообщений.")

    logger.info("Бот успешно запущен и готов к работе!")
    logger.info("=" * 50)

    # Запуск фоновой задачи очистки мёртвых записей (каждый час)
    asyncio.create_task(_periodic_cleanup())


async def _periodic_cleanup():
    """Фоновая задача: очистка rate-limit и сессий раз в час."""
    while True:
        try:
            await asyncio.sleep(3600)  # 1 час
            _cleanup_stale_entries()
            logger.info("🧹 Фоновая очистка мёртвых записей выполнена")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning(f"⚠️ Ошибка фоновой очистки: {e}")


async def on_shutdown(bot: Bot):
    """Действия при остановке бота."""
    logger.info("Бот останавливается...")
    # Остановка yappi-профайлера (если был запущен)
    _stop_yappi()
    # Очистка всех временных файлов
    file_service.cleanup_old_files(max_age_hours=0)
    logger.info("Временные файлы очищены.")
    logger.info("Бот остановлен.")


async def main():
    """Точка входа."""
    # Настройка сессии с прокси, если включено
    session = None
    if config.PROXY_ENABLED and config.PROXY_URL:
        try:
            session = AiohttpSession(proxy=config.PROXY_URL)
            logger.info("🌐 Прокси включён (SOCKS5)")
        except Exception as e:
            logger.warning(f"⚠️ Не удалось настроить прокси: {e}. Запуск без прокси.")

    bot = Bot(
        token=config.BOT_TOKEN,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()

    # Регистрируем профайлер (замеряет все хендлеры)
    dp.message.middleware(ProfilingMiddleware())
    dp.callback_query.middleware(ProfilingMiddleware())

    # Регистрируем роутеры (порядок важен: специфичные → общие)
    dp.include_router(commands_router)
    dp.include_router(admin_router)
    dp.include_router(documents_router)
    dp.include_router(callbacks_router)
    dp.include_router(text_router)

    # Регистрируем обработчики запуска/остановки
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    logger.info("Запуск polling...")
    try:
        await dp.start_polling(bot)
    except (KeyboardInterrupt, SystemExit):
        logger.info("Получен сигнал остановки.")
    finally:
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен пользователем.")
    except Exception as e:
        logger.exception(f"Критическая ошибка: {e}")
        sys.exit(1)
