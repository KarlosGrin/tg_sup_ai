"""
Callback-обработчики: cancel, file_select, action, confirm, format.
"""

import logging
from pathlib import Path

from aiogram import Router
from aiogram.types import CallbackQuery

from handlers.common import (
    _execute_code,
    _get_session,
    _process_action,
    _send_report,
    _send_result_file,
)
from utils.keyboards import (
    format_selection_keyboard,
    quick_actions_keyboard,
)

logger = logging.getLogger(__name__)

router = Router()


@router.callback_query(lambda c: c.data == "cancel")
async def callback_cancel(callback: CallbackQuery):
    """Отмена любого действия."""
    user_id = callback.from_user.id
    session = _get_session(user_id)
    session.pop("pending_action", None)
    session.pop("pending_code", None)
    await callback.message.edit_text("❌ Действие отменено.")
    await callback.answer()


@router.callback_query(lambda c: c.data.startswith("file_select:"))
async def callback_file_select(callback: CallbackQuery):
    """Выбор активного файла из списка загруженных."""
    user_id = callback.from_user.id
    index = int(callback.data.split(":", 1)[1])
    session = _get_session(user_id)
    file_paths = session["file_paths"]

    if index >= len(file_paths):
        await callback.answer("❌ Файл не найден.", show_alert=True)
        return

    session["active_file_index"] = index
    selected = file_paths[index]
    name = Path(selected).name
    parts = name.split("_", 1)
    clean_name = parts[1] if len(parts) > 1 else name

    await callback.message.edit_text(
        f"✅ Выбран файл: `{clean_name}`\n\nЧто сделать с файлом?",
        reply_markup=quick_actions_keyboard(),
    )
    await callback.answer()


@router.callback_query(lambda c: c.data.startswith("action:"))
async def callback_action(callback: CallbackQuery):
    """Обработка быстрых действий."""
    user_id = callback.from_user.id
    action = callback.data.split(":", 1)[1]
    session = _get_session(user_id)
    file_paths = session.get("file_paths", [])
    if not file_paths:
        await callback.message.edit_text("📤 Сначала загрузите файл.")
        await callback.answer()
        return

    # Определяем исходное расширение для детекта целевого
    active_index = session.get("active_file_index", 0)
    if active_index >= len(file_paths):
        active_index = 0
    if action in ("filter", "chart", "pivot"):
        templates = {
            "filter": "🔍 Что именно отфильтровать? Напишите условие.",
            "chart": "📊 Какой график построить? Опишите оси и тип.",
            "pivot": "📋 Какие поля для сводной таблицы? Напишите строки, колонки и значения.",
        }
        session["pending_action"] = {"type": action}
        await callback.message.edit_text(templates[action])
        await callback.answer()

    elif action == "save_docx":
        command = "Сохрани данные в формате DOCX (Word)"
        await _process_action(callback.message, user_id, command, require_confirm=False)

    elif action == "save_xlsx":
        command = "Сохрани данные в формате XLSX (Excel)"
        await _process_action(callback.message, user_id, command, require_confirm=False)

    elif action == "save_txt":
        command = "Сохрани данные в формате TXT"
        await _process_action(callback.message, user_id, command, require_confirm=False)

    else:
        await callback.message.edit_text(
            "💾 В какой формат конвертировать?",
            reply_markup=format_selection_keyboard(),
        )

    await callback.answer()


@router.callback_query(lambda c: c.data.startswith("confirm:"))
async def callback_confirm(callback: CallbackQuery):
    """Подтверждение или отмена выполнения кода."""
    user_id = callback.from_user.id
    choice = callback.data.split(":", 1)[1]
    session = _get_session(user_id)
    pending = session.pop("pending_code", None)

    if not pending:
        await callback.message.edit_text("⏳ Время ожидания истекло. Отправьте запрос заново.")
        await callback.answer()
        return

    if choice == "no":
        await callback.message.edit_text("❌ Выполнение кода отменено.")
        await callback.answer()
        return

    # choice == "yes"
    code = pending["code"]
    output_path = pending["output_path"]
    explanation = pending.get("explanation", "")
    analysis = pending.get("analysis", "")

    await callback.message.edit_text("⏳ Выполняю код...")

    execution_result = await _execute_code(
        code, output_path,
        input_path=pending.get("input_path"),
    )

    await _send_report(callback.message, analysis, explanation, execution_result)
    await _send_result_file(callback.message, user_id, output_path, execution_result, session)
    await callback.answer()


@router.callback_query(lambda c: c.data.startswith("format:"))
async def callback_format(callback: CallbackQuery):
    """Конвертация в выбранный формат."""
    user_id = callback.from_user.id
    target_format = callback.data.split(":", 1)[1]
    session = _get_session(user_id)
    file_paths = session.get("file_paths", [])
    if not file_paths:
        await callback.message.edit_text("📤 Сначала загрузите файл.")
        await callback.answer()
        return

    target_ext = f".{target_format}"
    source_ext = Path(file_paths[0]).suffix.lower()

    if target_ext == source_ext:
        await callback.message.edit_text("⚠️ Файл уже в этом формате.")
        await callback.answer()
        return

    command = f"Сконвертируй данные в формат {target_format}"
    await _process_action(callback.message, user_id, command, require_confirm=False)
    await callback.answer()
