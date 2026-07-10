"""
Автоматический профайлер для бота.
Замеряет время обработки хендлеров, AI-запросов, выполнения кода.
Логирует всё, что дольше порога. Собирает статистику для /perf.

Prometheus-метрики:
- bot_files_uploaded_total{ext} — загруженные файлы по расширениям
- bot_ai_requests_total{provider,status} — AI-запросы (ok/error)
- bot_code_exec_seconds — гистограмма времени выполнения кода
- bot_active_users — текущее количество активных пользователей
"""

import hashlib
import logging
import statistics
import time
from collections import defaultdict

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

logger = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════════
# Prometheus-метрики
# ════════════════════════════════════════════════════════════════

try:
    from prometheus_client import Counter, Histogram, Gauge

    files_uploaded = Counter(
        'bot_files_uploaded_total',
        'Всего загружено файлов',
        ['ext'],
    )
    ai_requests = Counter(
        'bot_ai_requests_total',
        'Запросы к AI провайдеру',
        ['provider', 'status'],
    )
    code_exec_seconds = Histogram(
        'bot_code_exec_seconds',
        'Время выполнения кода в песочнице (сек)',
        buckets=[.1, .5, 1, 2, 5, 10, 30, 60, 120],
    )
    active_users = Gauge(
        'bot_active_users',
        'Активных пользователей сейчас',
    )
    _prometheus_enabled = True
except ImportError:
    # fallback: заглушки если prometheus_client не установлен
    class _CounterStub:
        def labels(self, **kw): return self
        def inc(self, n=1): pass
    class _HistogramStub:
        def labels(self, **kw): return self
        def observe(self, n): pass
    class _GaugeStub:
        def set(self, n): pass
        def inc(self, n=1): pass
        def dec(self, n=1): pass

    files_uploaded = _CounterStub()
    ai_requests = _CounterStub()
    code_exec_seconds = _HistogramStub()
    active_users = _GaugeStub()
    _prometheus_enabled = False

# ════════════════════════════════════════════════════════════════
# Хранилище статистики
# ════════════════════════════════════════════════════════════════

_stats: dict[str, list[float]] = defaultdict(list)
_MAX_SAMPLES = 1000  # храним не больше 1000 замеров на точку


def _anonymize(user_id: int | None) -> str:
    """Анонимизировать user_id: первые 4 символа sha256."""
    from config import config as _cfg
    if user_id is None or not _cfg.LOG_ANONYMIZE_USER_ID:
        return str(user_id) if user_id else "?"
    return hashlib.sha256(str(user_id).encode()).hexdigest()[:8]


def _record(label: str, duration: float):
    """Записать замер в статистику."""
    samples = _stats[label]
    samples.append(duration)
    if len(samples) > _MAX_SAMPLES:
        samples.pop(0)


def get_stats() -> dict:
    """Вернуть агрегированную статистику по всем точкам."""
    result = {}
    for label, samples in sorted(_stats.items()):
        if not samples:
            continue
        sorted_s = sorted(samples)
        n = len(sorted_s)
        result[label] = {
            "count": n,
            "total": sum(sorted_s),
            "mean": statistics.mean(sorted_s),
            "median": statistics.median(sorted_s),
            "min": sorted_s[0],
            "max": sorted_s[-1],
            "p95": sorted_s[int(n * 0.95)] if n > 20 else sorted_s[-1],
        }
    return result


# ════════════════════════════════════════════════════════════════
# Middleware — замеряет ВСЕ хендлеры
# ════════════════════════════════════════════════════════════════

class ProfilingMiddleware(BaseMiddleware):
    """
    Middleware, замеряющий время обработки каждого апдейта.
    Логирует всё, что дольше SLOW_THRESHOLD секунд.
    User-ID анонимизируется (sha256[:8]) если LOG_ANONYMIZE_USER_ID=true.

    Prometheus: обновляет active_users на каждом событии.
    """

    SLOW_THRESHOLD = 1.0

    async def __call__(self, handler, event: TelegramObject, data: dict):
        start = time.perf_counter()
        result = await handler(event, data)
        duration = time.perf_counter() - start

        event_type = type(event).__name__
        _record(f"handler.{event_type}", duration)

        # Prometheus: обновляем активных пользователей
        from handlers.common import user_sessions
        active_users.set(len(user_sessions))

        if duration > self.SLOW_THRESHOLD:
            user_id = None
            if hasattr(event, "from_user") and event.from_user:
                user_id = event.from_user.id
            elif hasattr(event, "message") and event.message and event.message.from_user:
                user_id = event.message.from_user.id

            logger.warning(
                "🐢 SLOW %s: %.2f сек (user=%s, update_id=%s)",
                event_type, duration, _anonymize(user_id), getattr(event, "update_id", "?"),
            )
        return result


# ════════════════════════════════════════════════════════════════
# Timer — контекстный менеджер для замеров отдельных операций
# ════════════════════════════════════════════════════════════════

class Timer:
    """Контекстный менеджер для точечных замеров внутри хендлеров.

    Пример:
        with Timer('ai.generate_code', log_slow=2.0):
            result = ai_service.generate_code(...)
    """

    def __init__(self, label: str, log_slow: float = 1.0):
        self.label = f"op.{label}"
        self.log_slow = log_slow

    def __enter__(self):
        self.start = time.perf_counter()
        return self

    def __exit__(self, *args):
        duration = time.perf_counter() - self.start
        _record(self.label, duration)
        if duration > self.log_slow:
            logger.info("⏱ %s: %.2f сек", self.label, duration)


# ════════════════════════════════════════════════════════════════
# reset_stats — сброс накопленной статистики
# ════════════════════════════════════════════════════════════════

def reset_stats():
    """Сбросить все накопленные замеры."""
    _stats.clear()
