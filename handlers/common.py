"""
Общие состояние и хелперы для всех роутеров.
"""

import logging
import time

from config import config

logger = logging.getLogger(__name__)

# === Регистрация известных пользователей ===
_known_users: set[int] = set()


def _track_user(user_id: int):
    """Запомнить user_id для /broadcast и статистики."""
    _known_users.add(user_id)


# === Rate Limiting ===
_user_request_times: dict[int, list[float]] = {}
_user_upload_times: dict[int, list[float]] = {}


def _check_rate_limit(user_id: int, times: dict[int, list[float]], max_count: int, window_sec: int) -> bool:
    """Проверить, не превышен ли лимит запросов."""
    now = time.time()
    if user_id not in times:
        times[user_id] = []
    times[user_id] = [t for t in times[user_id] if now - t < window_sec]
    if len(times[user_id]) >= max_count:
        return False
    times[user_id].append(now)
    return True


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