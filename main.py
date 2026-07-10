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
import time

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

    # File handler (текстовый, с ротацией)
    loguru_logger.add(
        config.LOG_FILE,
        format="{time:YYYY-MM-DD HH:mm:ss} | {level:^7} | {name}:{function}:{line} | {message}",
        level=config.LOG_LEVEL,
        rotation=config.LOG_ROTATION,
        retention=config.LOG_RETENTION,
        encoding="utf-8",
        compression="gz",
    )

    # JSON handler (для Loki/ELK) — использует встроенную сериализацию loguru
    loguru_logger.add(
        "logs/bot.json",
        format="{time:YYYY-MM-DDTHH:mm:ss.SSSSSS}Z | {level} | {name}:{function}:{line} | {message}",
        level=config.LOG_LEVEL,
        rotation="100 MB",
        retention="7 days",
        encoding="utf-8",
        compression="gz",
        serialize=True,
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
            logger.warning("⚠️ Не удалось инициализировать Sentry: {}", e)


# ════════════════════════════════════════════════════════════════
# FastAPI health endpoint + Prometheus metrics
# ════════════════════════════════════════════════════════════════

_health_server = None
_start_time = 0.0

# ════════════════════════════════════════════════════════════════
# HTML-шаблон дашборда (не f-string — %-formatting, чтобы избежать
# конфликтов с { } в CSS и JavaScript)
# ════════════════════════════════════════════════════════════════
_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="utf-8">
    <title>tg_sup_ai — Dashboard</title>
    <style>
        * { margin:0; padding:0; box-sizing:border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #0d1117; color: #c9d1d9; padding: 2rem;
        }
        h1 { font-size:1.8rem; margin-bottom:.5rem; }
        h2 { font-size:1.3rem; margin:1.5rem 0 .8rem; color: #58a6ff; }
        .subtitle { color: #8b949e; margin-bottom:1.5rem; }
        .cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:1rem; margin-bottom:2rem; }
        .card {
            background: #161b22; border:1px solid #30363d; border-radius:8px;
            padding:1.2rem; text-align:center;
        }
        .card .label { font-size:.8rem; color:#8b949e; text-transform:uppercase; }
        .card .value { font-size:1.8rem; font-weight:700; margin-top:.3rem; }
        .card .value.green { color:#3fb950; }
        .card .value.blue  { color:#58a6ff; }
        .card .value.orange{ color:#d29922; }
        table {
            width:100%%; border-collapse:collapse; background:#161b22;
            border:1px solid #30363d; border-radius:8px; overflow:hidden;
        }
        th, td { padding:.7rem 1rem; text-align:left; border-bottom:1px solid #21262d; }
        th { background:#21262d; color:#8b949e; font-size:.8rem; text-transform:uppercase; }
        td.val { text-align:right; font-family:monospace; font-weight:600; }
        .links { margin-top:1.5rem; display:flex; gap:1rem; }
        .links a {
            color:#58a6ff; text-decoration:none; padding:.4rem .8rem;
            border:1px solid #30363d; border-radius:6px; font-size:.9rem;
        }
        .links a:hover { background:#1f2937; }
    </style>
</head>
<body>
    <h1>&#x1F916; tg_sup_ai</h1>
    <div class="subtitle">v1.0.0 &middot; Uptime: %s</div>

    <div class="cards">
        <div class="card">
            <div class="label">AI Requests</div>
            <div class="value blue" id="ai_reqs">&mdash;</div>
        </div>
        <div class="card">
            <div class="label">Files Uploaded</div>
            <div class="value green" id="files_up">&mdash;</div>
        </div>
        <div class="card">
            <div class="label">Active Users</div>
            <div class="value orange" id="active_users">&mdash;</div>
        </div>
        <div class="card">
            <div class="label">Uptime</div>
            <div class="value blue">%s</div>
        </div>
    </div>

    <h2>&#x1F4CA; Prometheus Metrics</h2>
    <table>
        <thead><tr><th>Metric</th><th>Labels</th><th>Value</th></tr></thead>
        <tbody>%s</tbody>
    </table>

    <h2>&#x1F50D; Health Checks</h2>
    <table>
        <thead><tr><th>Component</th><th>Status</th></tr></thead>
        <tbody>%s</tbody>
    </table>

    <div class="links">
        <a href="/metrics">&#x1F4C8; /metrics (Prometheus)</a>
        <a href="/health">&#x1F49A; /health (JSON)</a>
    </div>

    <script>
        fetch('/metrics').then(function(r) { return r.text(); }).then(function(text) {
            function get(name) {
                var re = new RegExp(name + '(?:\\\\{[^}]+\\\\})?\\\\s+([\\\\d.]+)');
                var m = text.match(re);
                return m ? parseFloat(m[1]) : 0;
            }
            document.getElementById('ai_reqs').textContent =
                get('bot_ai_requests_total').toLocaleString();
            document.getElementById('files_up').textContent =
                get('bot_files_uploaded_total').toLocaleString();
            document.getElementById('active_users').textContent =
                get('bot_active_users').toLocaleString();
        });
    </script>
</body>
</html>"""


async def _start_health_server() -> None:
    """Запуск FastAPI health/метрики сервера в фоне."""
    global _health_server
    try:
        import time as _time
        import uvicorn
        from fastapi import FastAPI
        from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse
        from prometheus_client import make_asgi_app, REGISTRY

        app = FastAPI(title="tg_sup_ai Health", docs_url=None, redoc_url=None)

        # ════════════════════════════════════════════════════════════════
        # Главная — редирект на дашборд
        # ════════════════════════════════════════════════════════════════
        @app.get("/")
        async def root():
            return RedirectResponse(url="/dashboard")

        # ════════════════════════════════════════════════════════════════
        # Дашборд — читаемая HTML-панель
        # ════════════════════════════════════════════════════════════════
        @app.get("/dashboard")
        async def dashboard():
            uptime_sec = _time.time() - _start_time if _start_time else 0
            uptime_str = "%dh %dm %ds" % (
                uptime_sec // 3600, (uptime_sec % 3600) // 60, uptime_sec % 60
            )

            # Собираем bot_* метрики
            metric_rows = ""
            for metric in REGISTRY.collect():
                if not metric.name.startswith("bot_"):
                    continue
                for sample in metric.samples:
                    if sample.name.endswith("_created") or sample.name.endswith("_bucket"):
                        continue
                    label_str = ", ".join("%s=%s" % (k, v) for k, v in sample.labels.items()) if sample.labels else ""
                    metric_rows += "<tr><td><code>%s</code></td><td>%s</td><td class='val'>%.1f</td></tr>" % (
                        sample.name, label_str, sample.value,
                    )

            # Проверки здоровья
            checks = []
            if config.REDIS_ENABLED:
                try:
                    from redis.asyncio import Redis
                    r = Redis.from_url(config.REDIS_URL)
                    await r.ping()
                    await r.close()
                    checks.append(("Redis", "\U0001f7e2 ok"))
                except Exception:
                    checks.append(("Redis", "\U0001f534 error"))
            else:
                checks.append(("Redis", "\u26aa disabled"))

            checks.append(("Sentry", "\U0001f7e2 ok" if _sentry_initialized else ("\U0001f534 error" if config.SENTRY_ENABLED else "\u26aa disabled")))
            checks.append(("Docker", "\U0001f7e2 enabled" if config.DOCKER_ENABLED else "\U0001f7e1 disabled"))
            checks.append(("AI Provider", "\U0001f535 " + config.AI_PROVIDER))
            checks.append(("Exec Mode", "\U0001f535 " + config.EXECUTION_MODE))

            health_rows = "".join("<tr><td>%s</td><td>%s</td></tr>" % c for c in checks)

            html = _DASHBOARD_HTML % (
                uptime_str,
                uptime_str,
                metric_rows,
                health_rows,
            )
            return HTMLResponse(html)

        # ════════════════════════════════════════════════════════════════
        # Health check с проверкой зависимостей
        # ════════════════════════════════════════════════════════════════
        @app.get("/health")
        @app.get("/healthz")
        async def health():
            checks = {}
            all_ok = True

            # 1. Redis
            if config.REDIS_ENABLED:
                try:
                    from redis.asyncio import Redis
                    r = Redis.from_url(config.REDIS_URL)
                    await r.ping()
                    await r.close()
                    checks["redis"] = {"status": "ok"}
                except Exception as e:
                    checks["redis"] = {"status": "error", "detail": str(e)}
                    all_ok = False
            else:
                checks["redis"] = {"status": "disabled"}

            # 2. Sentry
            if config.SENTRY_ENABLED:
                checks["sentry"] = {
                    "status": "ok" if _sentry_initialized else "error",
                    "initialized": _sentry_initialized,
                }
                if not _sentry_initialized:
                    all_ok = False
            else:
                checks["sentry"] = {"status": "disabled"}

            # 3. Директории
            for d in [config.DOWNLOAD_DIR, config.PROCESSED_DIR]:
                from pathlib import Path
                p = Path(d)
                checks[f"dir_{d}"] = {
                    "status": "ok" if p.exists() else "error",
                    "exists": p.exists(),
                }
                if not p.exists():
                    all_ok = False

            # 4. Uptime
            uptime_sec = _time.time() - _start_time if _start_time else 0

            status_code = 200 if all_ok else 503
            return JSONResponse(
                {
                    "status": "ok" if all_ok else "degraded",
                    "service": "tg_sup_ai",
                    "version": "1.0.0",
                    "uptime_sec": round(uptime_sec, 1),
                    "checks": checks,
                },
                status_code=status_code,
            )

        @app.get("/readyz")
        async def ready():
            return {"status": "ready"}

        # Prometheus metrics (монтируем ASGI-приложение на /metrics)
        metrics_app = make_asgi_app()
        app.mount("/metrics", metrics_app)

        config_obj = uvicorn.Config(app, host="0.0.0.0", port=config.HEALTH_PORT, log_level="warning")
        _health_server = uvicorn.Server(config_obj)
        logger.info("🏥 Health-сервер запущен на порту {}", config.HEALTH_PORT)
        await _health_server.serve()
    except ImportError:
        logger.warning("⚠️ Health-сервер не запущен: fastapi/uvicorn не установлены (pip install fastapi uvicorn)")
    except Exception:
        logger.exception("⚠️ Health-сервер упал с ошибкой")


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
            logger.warning("⚠️ Redis недоступен ({}), используется in-memory storage: {}",
                           config.REDIS_URL, e)


# placeholder для yappi stop (заполняется в on_startup)
_stop_yappi = lambda: None


async def on_startup(bot: Bot):
    """Действия при запуске бота."""
    global _start_time
    _start_time = time.time()
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
