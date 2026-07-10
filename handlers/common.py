"""
Общие состояние и хелперы для всех роутеров.
"""

import logging
import time
from typing import Optional

from config import config

logger = logging.getLogger(__name__)

# === Регистрация известных пользователей ===
_known_users: set[int] = set()


def _track_user(user_id: int):
    """Запомнить user_id для /broadcast и статистики."""
    _known_users.add(user_id)


# === Rate Limiting (in-memory fallback + Redis) ===
_user_request_times: dict[int, list[float]] = {}
_user_upload_times: dict[int, list[float]] = {}

_redis_rate_limiter = None


async def _get_redis_rate_limiter():
    """Ленивая инициализация Redis для rate-limit."""
    global _redis_rate_limiter
    if _redis_rate_limiter is None and config.REDIS_ENABLED:
        try:
            from redis.asyncio import Redis
            _redis_rate_limiter = Redis.from_url(config.REDIS_URL, decode_responses=True)
            await _redis_rate_limiter.ping()
            logger.info("🗄️ Redis rate-limit инициализирован")
        except Exception as e:
            logger.warning("⚠️ Redis для rate-limit недоступен: %s", e)
            _redis_rate_limiter = False  # не пытаемся снова
    return _redis_rate_limiter if _redis_rate_limiter else None


async def _check_rate_limit_async(
    user_id: int, times: dict[int, list[float]], max_count: int, window_sec: int,
    prefix: str = "rl",
) -> bool:
    """Async rate-limit: использует Redis если доступен, иначе in-memory."""
    redis = await _get_redis_rate_limiter()
    if redis:
        return await _check_rate_limit_redis(redis, user_id, max_count, window_sec, prefix)
    return _check_rate_limit_sync(user_id, times, max_count, window_sec)


async def _check_rate_limit_redis(redis, user_id: int, max_count: int, window_sec: int, prefix: str) -> bool:
    """Rate-limit через Redis Sliding Window."""
    key = f"{prefix}:{user_id}"
    now = int(time.time())
    window_start = now - window_sec

    try:
        pipe = redis.pipeline()
        pipe.zremrangebyscore(key, 0, window_start)  # удаляем старые
        pipe.zcard(key)  # считаем текущие
        pipe.expire(key, window_sec)  # обновляем TTL
        _, count, _ = await pipe.execute()

        if count >= max_count:
            return False

        await redis.zadd(key, {str(now): now})
        return True
    except Exception:
        return True  # fallback: пропускаем при ошибке Redis


def _check_rate_limit_sync(user_id: int, times: dict[int, list[float]], max_count: int, window_sec: int) -> bool:
    """Sync in-memory rate-limit (fallback)."""
    now = time.time()
    if user_id not in times:
        times[user_id] = []
    times[user_id] = [t for t in times[user_id] if now - t < window_sec]
    if len(times[user_id]) >= max_count:
        return False
    times[user_id].append(now)
    return True


def _check_rate_limit(user_id: int, times: dict[int, list[float]], max_count: int, window_sec: int) -> bool:
    """Синхронный rate-limit (in-memory)."""
    return _check_rate_limit_sync(user_id, times, max_count, window_sec)


def _is_admin(user_id: int) -> bool:
    """Проверить, является ли пользователь администратором."""
    return user_id in config.ADMIN_IDS if config.ADMIN_IDS else False


def _cleanup_stale_entries():
    """Периодически чистит мёртвые записи rate-limit и сессий."""
    now = time.time()
    for times_dict in (_user_request_times, _user_upload_times):
        for uid in list(times_dict.keys()):
            stamps = times_dict[uid]
            if not stamps or (now - max(stamps)) > 3600:
                del times_dict[uid]
    for uid in list(user_sessions.keys()):
        sess = user_sessions[uid]
        if not sess.get("file_paths") and uid not in _known_users:
            del user_sessions[uid]


# === Сессии пользователей ===
user_sessions: dict[int, dict] = {}


def _get_session(user_id: int) -> dict:
    """Получить или создать сессию пользователя."""
    _track_user(user_id)
    if user_id not in user_sessions:
        user_sessions[user_id] = {
            "file_paths": [],
            "last_code": "",
        }
    return user_sessions[user_id]