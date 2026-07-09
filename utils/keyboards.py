"""
Inline-клавиатуры для Telegram бота.
"""

from pathlib import Path

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def file_selection_keyboard(file_paths: list[str]) -> InlineKeyboardMarkup:
    """Клавиатура для выбора активного файла из списка загруженных."""
    builder = InlineKeyboardBuilder()
    for i, fp in enumerate(file_paths):
        name = Path(fp).name
        # Убираем UUID-префикс вида "32hex_originalname.ext"
        parts = name.split("_", 1)
        clean_name = parts[1] if len(parts) > 1 else name
        builder.button(text=f"📄 {clean_name[:35]}", callback_data=f"file_select:{i}")
    builder.button(text="❌ Отмена", callback_data="cancel")
    builder.adjust(1)
    return builder.as_markup()


def sheet_selection_keyboard(sheets: list[str]) -> InlineKeyboardMarkup:
    """Клавиатура для выбора листа Excel."""
    builder = InlineKeyboardBuilder()
    for sheet in sheets:
        builder.button(text=f"📑 {sheet[:30]}", callback_data=f"sheet_select:{sheet}")
    builder.button(text="❌ Отмена", callback_data="cancel")
    builder.adjust(1)
    return builder.as_markup()


def quick_actions_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура быстрых действий после загрузки файла."""
    builder = InlineKeyboardBuilder()
    builder.button(text="🔍 Отфильтровать", callback_data="action:filter")
    builder.button(text="📊 Построить график", callback_data="action:chart")
    builder.button(text="📋 Сводная таблица", callback_data="action:pivot")
    builder.button(text="📝 Сохранить как .docx", callback_data="action:save_docx")
    builder.button(text="📗 Сохранить как .xlsx", callback_data="action:save_xlsx")
    builder.button(text="📃 Сохранить как .txt", callback_data="action:save_txt")
    builder.adjust(2, 2, 2)
    return builder.as_markup()


def confirmation_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура подтверждения выполнения сгенерированного кода."""
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Выполнить", callback_data="confirm:yes")
    builder.button(text="❌ Отменить", callback_data="confirm:no")
    builder.adjust(2)
    return builder.as_markup()


def format_selection_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура выбора целевого формата для конвертации."""
    builder = InlineKeyboardBuilder()
    formats = [
        ("📗 Excel (.xlsx)", "format:xlsx"),
        ("📘 Excel (.xls)", "format:xls"),
        ("📃 CSV (.csv)", "format:csv"),
        ("📊  ODS (.ods)", "format:ods"),
        ("📝 Word (.docx)", "format:docx"),
        ("📄 TXT (.txt)", "format:txt"),
    ]
    for label, cb_data in formats:
        builder.button(text=label, callback_data=cb_data)
    builder.button(text="❌ Отмена", callback_data="cancel")
    builder.adjust(2, 2, 2, 1)
    return builder.as_markup()
