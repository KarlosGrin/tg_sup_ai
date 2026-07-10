"""
Обработчик текстовых сообщений: handle_text + fallback.
"""

import logging

from aiogram import F, Router
from aiogram.types import Message

from config import config
from handlers.common import (
    _check_rate_limit,
    _get_session,
    _user_request_times,
)
from services.pipeline import process_action

logger = logging.getLogger(__name__)

router = Router()


@router.message(F.text)
async def handle_text(message: Message):
    """Главный текстовый обработчик: команды пользователя."""
    user_id = message.from_user.id
    text = message.text.strip()

    # === Проверка отложенного действия (уточнение к фильтру/графику/сводной) ===
    session = _get_session(user_id)
    pending_action = session.pop("pending_action", None)
    if pending_action:
        action_type = pending_action["type"]
        templates = {
            "filter": f"Отфильтруй данные. Условие: {text}",
            "chart": f"Построй график или диаграмму. {text}",
            "pivot": f"Сделай сводную таблицу. {text}",
        }
        command = templates.get(action_type, text)
    else:
        command = text

    file_paths = session.get("file_paths", [])
    if not file_paths:
        await message.answer(
            "📤 Сначала загрузите файл, с которым нужно работать!\n"
            "Или напишите вопрос, и я постараюсь помочь."
        )
        return

    # Rate limit
    if not _check_rate_limit(user_id, _user_request_times, config.RATE_LIMIT_REQUESTS_PER_MIN, 60):
        await message.answer(f"⏳ Слишком много запросов. Лимит: {config.RATE_LIMIT_REQUESTS_PER_MIN} в минуту.")
        return

    await process_action(message, user_id, command, require_confirm=True)


@router.message()
async def handle_unknown(message: Message):
    """Fallback: неподдерживаемый тип сообщения."""
    await message.answer(
        "📤 Отправьте мне файл (Excel, CSV, Word, TXT) или напишите команду.\n"
        "Используйте /help для справки."
    )
