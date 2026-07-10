"""
Обработчик загрузки документов.
"""

import logging
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.types import Message

from config import config
from handlers.common import (
    _check_rate_limit,
    _get_session,
    _user_upload_times,
)
from services.file_service import file_service
from utils.keyboards import quick_actions_keyboard

logger = logging.getLogger(__name__)

router = Router()


@router.message(F.document)
async def handle_document(message: Message, bot: Bot):
    """Обработчик загрузки документов."""
    user_id = message.from_user.id
    document = message.document
    file_name = document.file_name or "unknown"

    # Rate limit: не более N загрузок в час
    if not _check_rate_limit(user_id, _user_upload_times, config.RATE_LIMIT_FILE_UPLOADS_PER_HOUR, 3600):
        await message.answer(f"⏳ Слишком много загрузок. Лимит: {config.RATE_LIMIT_FILE_UPLOADS_PER_HOUR} в час.")
        return

    ext = Path(file_name).suffix.lower()
    if ext not in file_service.ALLOWED_EXTENSIONS:
        await message.answer(
            f"❌ Формат <code>{ext}</code> не поддерживается.\n"
            f"Поддерживаемые: {', '.join(file_service.ALLOWED_EXTENSIONS)}",
            parse_mode="HTML",
        )
        return

    if document.file_size and document.file_size > config.MAX_FILE_SIZE_MB * 1024 * 1024:
        await message.answer(f"❌ Файл слишком большой (макс. {config.MAX_FILE_SIZE_MB} МБ)")
        return

    status_msg = await message.answer(f"⏳ Скачиваю файл <code>{file_name}</code>...", parse_mode="HTML")

    try:
        saved_path = await file_service.download_file(document, user_id, bot=bot)
        if not saved_path:
            await status_msg.edit_text("❌ Не удалось сохранить файл.")
            return

        session = _get_session(user_id)
        session["file_paths"].append(str(saved_path))

        await status_msg.edit_text(f"🔍 Анализирую структуру <code>{file_name}</code>...", parse_mode="HTML")
        summary = file_service.get_file_summary(str(saved_path))

        await status_msg.edit_text(
            f"✅ Файл загружен и проанализирован!\n\n{summary}\n\n💡 Что сделать с файлом?",
            parse_mode="HTML",
            reply_markup=quick_actions_keyboard(),
        )
    except Exception:
        import traceback
        traceback.print_exc()
        await status_msg.edit_text("❌ Ошибка при загрузке файла. Проверьте формат и попробуйте снова.")
