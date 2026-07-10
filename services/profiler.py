"""
Автоматический профайлер для бота.
Замеряет время обработки хендлеров, AI-запросов, выполнения кода.
Логирует всё, что дольше порога. Собирает статистику для /perf.
"""

import logging
import statistics
import time
from collections import defaultdict

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

logger = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════════
# Хранилище статистики
# ════════════════════════════════════════════════════════════════

_stats: dict[str, list[float]] = defaultdict(list)
_MAX_SAMPLES = 1000  # храним не больше 1000 замеров на точку


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
    """

    SLOW_THRESHOLD = 1.0

    async def __call__(self, handler, event: TelegramObject, data: dict):
        start = time.perf_counter()
        result = await handler(event, data)
        duration = time.perf_counter() - start

        event_type = type(event).__name__
        _record(f"handler.{event_type}", duration)

        if duration > self.SLOW_THRESHOLD:
            user_id = None
            if hasattr(event, "from_user") and event.from_user:
                user_id = event.from_user.id
            elif hasattr(event, "message") and event.message and event.message.from_user:
                user_id = event.message.from_user.id

            logger.warning(
                "🐢 SLOW %s: %.2f сек (user=%s, update_id=%s)",
                event_type, duration, user_id, getattr(event, "update_id", "?"),
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
