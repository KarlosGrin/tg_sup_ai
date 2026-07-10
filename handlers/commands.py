"""
Команды: /start, /help, /files, /clear, /status.
"""

import logging
from pathlib import Path

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
from aiogram.utils.formatting import Bold, Code, Italic, Text

from handlers.common import (
    _get_session,
    _is_admin,
    user_sessions,
)
from services.file_service import file_service

logger = logging.getLogger(__name__)

router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message):
    """Обработчик команды /start. Без дублирования текста."""
    parts = [
        Bold("👋 Привет! Я AI-помощник для работы с данными.\n\n"),
        "Я умею:\n",
        "📊 Анализировать Excel, CSV, Word файлы\n",
        "✏️ Редактировать и трансформировать данные по вашим командам\n",
        "🔗 Объединять таблицы (VLOOKUP, JOIN)\n",
        "🧮 Создавать расчётные колонки\n",
        "📋 Фильтровать, сортировать, удалять дубликаты\n\n",
        "📤 ", Bold("Просто загрузи файл"), " и напиши, что нужно сделать!\n\n",
        "Доступные команды:\n",
        Code("/start"), " — показать это сообщение\n",
        Code("/help"), " — справка\n",
        Code("/files"), " — список загруженных файлов\n",
        Code("/clear"), " — очистить сессию\n",
        Code("/status"), " — статус сессии\n",
    ]

    if _is_admin(message.from_user.id):
        parts += [
            "\n",
            Bold("🛠 Команды администратора:\n"),
            Code("/admin"), " — панель администратора\n",
            Code("/stats"), " — статистика бота\n",
            Code("/broadcast"), " — рассылка пользователям\n",
        ]

    await message.answer(Text(*parts).as_html())


@router.message(Command("help"))
async def cmd_help(message: Message):
    """Обработчик команды /help."""
    help_text = Text(
        Bold("📖 Справка по использованию\n\n"),
        Bold("1. Загрузите файл\n"),
        "Отправьте боту файл формата .xlsx, .csv, .docx и т.д.\n"
        "Я прочитаю его структуру и сообщу, что вижу.\n\n",
        Bold("2. Дайте команду\n"),
        "Просто напишите, что нужно сделать, например:\n",
        "• ", Italic('"Посчитай сумму продаж по месяцам"'), "\n",
        "• ", Italic('"Добавь колонку с процентом от общей суммы"'), "\n",
        "• ", Italic('"Объедини эту таблицу с предыдущей по артикулу"'), "\n",
        "• ", Italic('"Удали дубликаты и отсортируй по дате"'), "\n\n",
        Bold("3. Получите результат\n"),
        "Я обработаю данные и пришлю готовый файл!\n\n",
        Bold("Поддерживаемые форматы:\n"),
        "📊 Excel: .xlsx, .xls, .ods\n",
        "📃 Данные: .csv\n",
        "📝 Документы: .docx\n",
        "📄 Текст: .txt, .json, .xml\n\n",
        Bold("Команды:\n"),
        Code("/start"), " — приветствие\n",
        Code("/files"), " — список файлов в сессии\n",
        Code("/clear"), " — очистить все файлы и историю\n",
        Code("/status"), " — информация о текущей сессии\n",
    )
    await message.answer(help_text.as_html())


@router.message(Command("files"))
async def cmd_files(message: Message):
    """Показать список загруженных файлов с кнопками для выбора."""
    user_id = message.from_user.id
    session = _get_session(user_id)
    file_paths = session["file_paths"]

    if not file_paths:
        await message.answer("📭 Нет загруженных файлов. Отправьте мне файл!")
        return

    from utils.keyboards import file_selection_keyboard
    await message.answer(
        "📂 **Ваши файлы:**\n\nВыберите файл, чтобы продолжить:",
        reply_markup=file_selection_keyboard(file_paths),
    )


@router.message(Command("clear"))
async def cmd_clear(message: Message):
    """Очистить сессию пользователя."""
    user_id = message.from_user.id
    file_service.cleanup_user_files(user_id)
    user_sessions.pop(user_id, None)
    await message.answer("✅ Сессия очищена. Все файлы удалены.")


@router.message(Command("status"))
async def cmd_status(message: Message):
    """Показать статус сессии."""
    user_id = message.from_user.id
    session = _get_session(user_id)
    file_paths = session["file_paths"]

    status_lines = [
        Bold("📊 Статус сессии\n"),
        f"Файлов загружено: {len(file_paths)}",
        f"История сообщений: {len(session['history'])} записей",
    ]

    if file_paths:
        status_lines.append(f"\n{Bold('Файлы:')}")
        for fp in file_paths:
            size = Path(fp).stat().st_size
            status_lines.append(f"  • {Path(fp).name} ({size / 1024:.1f} КБ)")

    await message.answer(Text(*status_lines).as_html())
