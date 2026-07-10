"""
Главный файл Telegram бота — AI-ассистент для работы с Excel/Word файлами.

Запуск:
    python main.py

Переменные окружения (файл .env):
    BOT_TOKEN          — токен Telegram бота (обязательно)
    CHANNEL_ID         — ID канала (опционально)
    OPENAI_API_KEY     — ключ OpenAI API
    AI_PROVIDER        — "openai" (по умолчанию) или "gemini"
    EXECUTION_MODE     — "local" (по умолчанию) или "assistants"
    ADMIN_IDS          — ID администраторов через запятую
    SENTRY_DSN         — DSN для Sentry (опционально)
    REDIS_URL          — Redis URL для FSM (опционально)
    HEALTH_PORT        — порт для health endpoint (по умолчанию 8080)
"""

import asyncio
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode

from config import config
from handlers.admin import router as admin_router
from handlers.callbacks import router as callbacks_router
from handlers.commands import router as commands_router
from handlers.common import _cleanup_stale_entries
from handlers.documents import router as documents_router
from handlers.text import router as text_router
from services.file_service import file_service
from services.profiler import ProfilingMiddleware
from utils.profiler_decorator import start_yappi

# ════════════════════════════════════════════════════════════════
# Loguru — структурированное логирование
# ════════════════════════════════════════════════════════════════
import logging

from loguru import logger as loguru_logger


class _InterceptHandler(logging.Handler):
    """Перенаправляет стандартные logging-сообщения в loguru."""

    def emit(self, record: logging.Record) -> None:
        try:
            level = loguru_logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        frame, depth = logging.currentframe(), 2
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1
        loguru_logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


def _setup_logging() -> None:
    """Настройка loguru с ротацией и форматированием."""
    loguru_logger.remove()  # убираем default stderr handler

    # Формат: время | уровень | имя_модуля | сообщение
    fmt = (
        "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
        "<level>{level:^7}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
        "<level>{message}</level>"
    )

    # Console handler (цветной)
    loguru_logger.add(
        sys.stdout,
        format=fmt,
        level=config.LOG_LEVEL,
        colorize=True,
    )

    # File handler (с ротацией)
    loguru_logger.add(
        config.LOG_FILE,
        format="{time:YYYY-MM-DD HH:mm:ss} | {level:^7} | {name}:{function}:{line} | {message}",
        level=config.LOG_LEVEL,
        rotation=config.LOG_ROTATION,
        retention=config.LOG_RETENTION,
        encoding="utf-8",
        compression="gz",
    )

    # Перехватываем стандартное logging → loguru
    logging.basicConfig(handlers=[_InterceptHandler()], level=0, force=True)

    # Отключаем шумные логгеры библиотек
    for noisy in ("aiogram", "httpx", "httpcore", "urllib3", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


_setup_logging()
logger = loguru_logger

# ════════════════════════════════════════════════════════════════
# Sentry — централизованный сбор ошибок (опционально)
# ════════════════════════════════════════════════════════════════

_sentry_initialized = False


def _init_sentry() -> None:
    """Инициализация Sentry, если настроен SENTRY_DSN."""
    global _sentry_initialized
    if config.SENTRY_ENABLED and config.SENTRY_DSN:
        try:
            import sentry_sdk

            sentry_sdk.init(
                dsn=config.SENTRY_DSN,
                environment="production" if config.DOCKER_ENABLED else "development",
                traces_sample_rate=0.1,
                profiles_sample_rate=0.1,
                enable_tracing=True,
            )
            _sentry_initialized = True
            logger.info("🗼 Sentry инициализирован")
        except Exception as e:
            logger.warning("⚠️ Не удалось инициализировать Sentry: {e}")


# ════════════════════════════════════════════════════════════════
# FastAPI health endpoint + Prometheus metrics
# ════════════════════════════════════════════════════════════════

_health_server = None


async def _start_health_server() -> None:
    """Запуск FastAPI health/метрики сервера в фоне."""
    global _health_server
    try:
        import uvicorn
        from fastapi import FastAPI
        from prometheus_client import make_asgi_app

        app = FastAPI(title="tg_sup_ai Health", docs_url=None, redoc_url=None)

        # Health check
        @app.get("/health")
        @app.get("/healthz")
        async def health():
            return {"status": "ok", "service": "tg_sup_ai", "version": "1.0.0"}

        @app.get("/readyz")
        async def ready():
            return {"status": "ready"}

        # Prometheus metrics (монтируем ASGI-приложение на /metrics)
        metrics_app = make_asgi_app()
        app.mount("/metrics", metrics_app)

        config_obj = uvicorn.Config(app, host="0.0.0.0", port=config.HEALTH_PORT, log_level="warning")
        _health_server = uvicorn.Server(config_obj)
        logger.info("🏥 Health-сервер запущен на порту {port}", port=config.HEALTH_PORT)
        await _health_server.serve()
    except (ImportError, Exception) as e:
        logger.warning("⚠️ Health-сервер не запущен (fastapi/uvicorn не установлены): {e}")


# ════════════════════════════════════════════════════════════════
# Redis FSM (опционально)
# ════════════════════════════════════════════════════════════════

_redis_storage = None


async def _init_redis_storage() -> None:
    """Инициализация RedisStorage для FSM, если REDIS_ENABLED."""
    global _redis_storage
    if config.REDIS_ENABLED:
        try:
            from aiogram.fsm.storage.redis import RedisStorage, DefaultKeyBuilder
            from redis.asyncio import Redis

            redis = Redis.from_url(
                config.REDIS_URL,
                decode_responses=True,
            )
            await redis.ping()
            _redis_storage = RedisStorage(
                redis=redis,
                key_builder=DefaultKeyBuilder(with_destiny=True),
            )
            logger.info("🗄️ Redis FSM инициализирован: {url}", url=config.REDIS_URL)
        except Exception as e:
            logger.warning("⚠️ Redis недоступен ({url}), используется in-memory storage: {e}",
                           url=config.REDIS_URL, e=e)


# placeholder для yappi stop (заполняется в on_startup)
_stop_yappi = lambda: None


async def on_startup(bot: Bot):
    """Действия при запуске бота."""
    logger.info("=" * 50)
    logger.info("Бот запускается...")

    # Инициализация Sentry
    _init_sentry()

    # Запуск yappi-профайлера для async-кода (если ENABLE_PROFILING=true)
    global _stop_yappi
    if config.ENABLE_PROFILING:
        _stop_yappi = start_yappi()
        logger.info("📊 Async-профилирование ВКЛЮЧЕНО (yappi)")
    else:
        _stop_yappi = lambda: None

    # Проверка принудительного Docker для production
    if config.ENFORCE_DOCKER and not config.DOCKER_ENABLED:
        logger.error("⚠️" + "=" * 60)
        logger.error("⚠️ ENFORCE_DOCKER=true, но DOCKER_ENABLED=false!")
        logger.error("⚠️ Бот не может запуститься в production-режиме без Docker.")
        logger.error("⚠️ Установите Docker Desktop и выполните: docker compose build sandbox")
        logger.error("⚠️" + "=" * 60)
        raise RuntimeError("ENFORCE_DOCKER is set but Docker is not enabled. Aborting.")

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
    if config.CHANNEL_ID:
        try:
            chat = await bot.get_chat(int(config.CHANNEL_ID))
            logger.info(f"Канал: {chat.title} (ID: {chat.id})")
        except Exception as e:
            logger.warning(f"Не удалось получить информацию о канале {config.CHANNEL_ID}: {e}")
            logger.info("Бот будет работать в режиме личных сообщений.")
    else:
        logger.info("CHANNEL_ID не задан — бот работает в режиме личных сообщений.")

    logger.info("Бот успешно запущен и готов к работе!")
    logger.info("=" * 50)

    # Запуск фоновой задачи очистки мёртвых записей (каждый час)
    asyncio.create_task(_periodic_cleanup())

    # Запуск health-сервера
    asyncio.create_task(_start_health_server())

    # Инициализация Redis FSM
    await _init_redis_storage()


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

    # Остановка health-сервера
    global _health_server
    if _health_server:
        _health_server.should_exit = True
        logger.info("🏥 Health-сервер остановлен")

    # Закрытие Redis
    global _redis_storage
    if _redis_storage:
        await _redis_storage.close()
        logger.info("🗄️ Redis FSM закрыт")

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
    dp = Dispatcher(storage=_redis_storage)

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
