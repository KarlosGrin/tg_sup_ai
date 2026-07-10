"""
Middleware для профилирования и логирования медленных хендлеров.
"""
import logging
import time
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

logger = logging.getLogger(__name__)


class ProfilingMiddleware(BaseMiddleware):
    """Логирует хендлеры, выполнение которых занимает больше 1 секунды."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        start = time.perf_counter()
        try:
            return await handler(event, data)
        finally:
            duration = time.perf_counter() - start
            if duration > 1.0:
                logger.warning(
                    "Slow handler: %s took %.2f s.", type(event).__name__, duration
                )