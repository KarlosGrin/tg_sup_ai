"""
Обработчики сообщений и команд бота.
"""

import asyncio
import os
import time
from pathlib import Path
from typing import Optional

from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, FSInputFile
from aiogram.filters import Command, CommandStart
from aiogram.utils.formatting import Text, Bold, Italic, Code

from config import config
from services.file_service import file_service
from services.ai_service import ai_service
from services.code_executor import code_executor
from services.gemini_assistant import gemini_assistant
from utils.helpers import sanitize_for_markdown
from utils.keyboards import (
    file_selection_keyboard,
    quick_actions_keyboard,
    confirmation_keyboard,
    format_selection_keyboard,
)

router = Router()

# === Rate Limiting ===
_user_request_times: dict[int, list[float]] = {}
_user_upload_times: dict[int, list[float]] = {}


def _check_rate_limit(user_id: int, times: dict[int, list[float]], max_count: int, window_sec: int) -> bool:
    """Проверить, не превышен ли лимит запросов."""
    now = time.time()
    if user_id not in times:
        times[user_id] = []
    # Оставляем только запросы в окне
    times[user_id] = [t for t in times[user_id] if now - t < window_sec]
    if len(times[user_id]) >= max_count:
        return False  # лимит превышен
    times[user_id].append(now)
    return True


def _is_admin(user_id: int) -> bool:
    """Проверить, является ли пользователь администратором."""
    return user_id in config.ADMIN_IDS if config.ADMIN_IDS else False


# Временное хранилище состояний пользователей
user_sessions: dict[int, dict] = {}


def _get_session(user_id: int) -> dict:
    """Получить или создать сессию пользователя."""
    if user_id not in user_sessions:
        user_sessions[user_id] = {
            "file_paths": [],
            "last_code": "",
            "history": [],
        }
    return user_sessions[user_id]


@router.message(CommandStart())
async def cmd_start(message: Message):
    """Обработчик команды /start."""
    welcome_text = Text(
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
    )
    await message.answer(welcome_text.as_html())


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
        "• ", Italic("\"Посчитай сумму продаж по месяцам\""), "\n",
        "• ", Italic("\"Добавь колонку с процентом от общей суммы\""), "\n",
        "• ", Italic("\"Объедини эту таблицу с предыдущей по артикулу\""), "\n",
        "• ", Italic("\"Удали дубликаты и отсортируй по дате\""), "\n\n",
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
            f"❌ Формат `{ext}` не поддерживается.\n"
            f"Поддерживаемые: {', '.join(file_service.ALLOWED_EXTENSIONS)}",
            parse_mode="Markdown",
        )
        return

    if document.file_size and document.file_size > config.MAX_FILE_SIZE_MB * 1024 * 1024:
        await message.answer(f"❌ Файл слишком большой (макс. {config.MAX_FILE_SIZE_MB} МБ)")
        return

    status_msg = await message.answer(f"⏳ Скачиваю файл `{file_name}`...", parse_mode="Markdown")

    try:
        saved_path = await file_service.download_file(document, user_id, bot=bot)
        if not saved_path:
            await status_msg.edit_text("❌ Не удалось сохранить файл.")
            return

        session = _get_session(user_id)
        session["file_paths"].append(str(saved_path))

        await status_msg.edit_text(f"🔍 Анализирую структуру `{file_name}`...", parse_mode="Markdown")
        summary = file_service.get_file_summary(str(saved_path))

        await status_msg.edit_text(
            f"✅ Файл загружен и проанализирован!\n\n{summary}\n\n💡 Что сделать с файлом?",
            parse_mode="Markdown",
            reply_markup=quick_actions_keyboard(),
        )
    except Exception as e:
        await status_msg.edit_text(f"❌ Ошибка при загрузке файла: {e}")


# ===== CALLBACK QUERY HANDLERS =====


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
    """Обработка быстрых действий (фильтр, график, сводная, конвертация)."""
    user_id = callback.from_user.id
    action = callback.data.split(":", 1)[1]
    session = _get_session(user_id)
    file_paths = session["file_paths"]

    if not file_paths:
        await callback.message.edit_text("📭 Нет загруженных файлов. Сначала отправьте файл.")
        await callback.answer()
        return

    # Действия, требующие уточнения
    if action in ("filter", "chart", "pivot"):
        hints = {
            "filter": ("🔍 Отфильтровать данные", "оставить только строки, где сумма > 1000"),
            "chart": ("📊 Построить график", "столбчатую диаграмму продаж по месяцам"),
            "pivot": ("📋 Сводная таблица", "строки — регион, столбцы — месяц, значения — сумма"),
        }
        title, example = hints[action]
        session["pending_action"] = {"type": action}

        await callback.message.edit_text(
            f"**{title}**\n\n"
            f"Напишите условие в следующем сообщении.\n"
            f"Например: _{example}_"
        )
        await callback.answer()
        return

    # Действия без уточнения — сразу отправляем в AI
    action_commands = {
        "save_docx": "Сохрани все данные в формате Word (.docx). Сделай документ с таблицами и форматированием.",
        "save_xlsx": "Сохрани все данные в формате Excel (.xlsx).",
        "save_txt": "Сохрани все данные в текстовом формате (.txt).",
    }

    command = action_commands.get(action)
    if not command:
        await callback.answer("Неизвестное действие.", show_alert=True)
        return

    await callback.message.edit_text(f"⏳ Выполняю: {action_commands[action]}")
    await callback.answer()

    # Запускаем обработку через AI (без дополнительного подтверждения для конвертации)
    await _process_action(callback.message, user_id, command, require_confirm=False)


@router.callback_query(lambda c: c.data.startswith("confirm:"))
async def callback_confirm(callback: CallbackQuery):
    """Подтверждение или отмена выполнения сгенерированного кода."""
    user_id = callback.from_user.id
    decision = callback.data.split(":", 1)[1]
    session = _get_session(user_id)
    pending = session.get("pending_code")

    if not pending:
        await callback.message.edit_text("⚠️ Нет ожидающего подтверждения кода.")
        await callback.answer()
        return

    if decision == "no":
        session.pop("pending_code", None)
        await callback.message.edit_text("❌ Выполнение отменено.")
        await callback.answer()
        return

    # decision == "yes"
    await callback.message.edit_text("⚙️ **Выполняю код...**")
    await callback.answer()

    code = pending["code"]
    output_path = pending["output_path"]
    analysis = pending["analysis"]
    explanation = pending["explanation"]

    execution_result = await _execute_code(code, output_path, input_path=pending.get("input_path"))
    await _send_report(callback.message, analysis, explanation, execution_result)
    await _send_result_file(callback.message, user_id, output_path, execution_result, session)
    session.pop("pending_code", None)


@router.callback_query(lambda c: c.data.startswith("format:"))
async def callback_format(callback: CallbackQuery):
    """Конвертация файла в выбранный формат."""
    user_id = callback.from_user.id
    target_format = callback.data.split(":", 1)[1]
    session = _get_session(user_id)

    fmt_names = {
        "xlsx": "Excel (.xlsx)", "xls": "Excel (.xls)", "csv": "CSV",
        "ods": "ODS", "docx": "Word (.docx)", "txt": "TXT",
    }
    await callback.message.edit_text(f"⏳ Конвертирую в {fmt_names.get(target_format, target_format)}...")
    await callback.answer()

    command = (
        f"Сохрани данные в формате {target_format.upper()}. "
        f"Используй output_dir для создания файла с расширением .{target_format}. "
        f"Если это табличные данные — сохрани через pandas."
    )
    await _process_action(callback.message, user_id, command, require_confirm=False)


# ===== TEXT HANDLER =====


@router.message(F.text)
async def handle_text(message: Message):
    """
    Обработчик текстовых команд.
    Проверяет наличие отложенного действия (pending_action),
    отправляет запрос в AI и запрашивает подтверждение перед выполнением кода.
    """
    user_id = message.from_user.id
    text = message.text.strip()

    if not text:
        return

    session = _get_session(user_id)
    file_paths = session["file_paths"]

    # === Проверка отложенного действия (уточнение к фильтру/графику/сводной) ===
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

    if not file_paths:
        await message.answer(
            "📤 Сначала загрузите файл, с которым нужно работать!\n"
            "Или напишите вопрос, и я постараюсь помочь."
        )
        return

    # Rate limit: не более N запросов в минуту
    if not _check_rate_limit(user_id, _user_request_times, config.RATE_LIMIT_REQUESTS_PER_MIN, 60):
        await message.answer(f"⏳ Слишком много запросов. Лимит: {config.RATE_LIMIT_REQUESTS_PER_MIN} в минуту.")
        return

    status_msg = await message.answer("⏳ Анализирую ваш запрос...")

    try:
        ai_result = await _process_ai_request(user_id, command, file_paths)
        if ai_result is None:
            await status_msg.edit_text("❌ Не удалось получить ответ от AI. Попробуйте переформулировать запрос.")
            return

        analysis, code, explanation, output_path = ai_result
        session["last_code"] = code

        if not code and config.EXECUTION_MODE != "assistants":
            # AI ответил текстом (не кодом) — просто показываем ответ
            await status_msg.edit_text(
                f" **Ответ:**\n{sanitize_for_markdown(explanation)}"
            )
            return

        if config.EXECUTION_MODE == "assistants":
            # Режим песочницы Gemini — выполняем сразу (код генерирует сама Gemini)
            await status_msg.edit_text("⏳ Обрабатываю через песочницу Gemini...")
            execution_result = await _execute_code(code, output_path, input_path=file_paths[0] if file_paths else None)
            await _send_report(status_msg, analysis, explanation, execution_result)
            await _send_result_file(message, user_id, output_path, execution_result, session)
            return

        # Сохраняем код в ожидание подтверждения
        session["pending_code"] = {
            "analysis": analysis,
            "code": code,
            "explanation": explanation,
            "output_path": output_path,
            "input_path": file_paths[0] if file_paths else None,
        }

        await status_msg.edit_text(
            f" **Пояснение:**\n{sanitize_for_markdown(explanation)}\n\n"
            f"💻 Выполнить сгенерированный код?",
            reply_markup=confirmation_keyboard(),
        )

    except Exception as e:
        await status_msg.edit_text(f"❌ Критическая ошибка: {e}")
        import traceback
        traceback.print_exc()


# ===== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =====


async def _process_ai_request(user_id: int, command: str, file_paths: list[str]) -> Optional[tuple]:
    """Собрать контекст → отправить в AI → получить (analysis, code, explanation, output_path)."""

    primary_file = file_paths[0]
    input_path = primary_file

    primary_name = Path(primary_file).name
    parts = primary_name.split("_", 1)
    clean_name = parts[1] if len(parts) > 1 else primary_name
    base, ext = os.path.splitext(clean_name)
    output_name = f"{base}_result{ext}"
    output_path = str(Path(config.PROCESSED_DIR) / output_name)

    if config.EXECUTION_MODE == "assistants":
        # Режим песочницы Gemini: код не генерируем,
        # Gemini сама выполнит всю обработку в _execute_code
        return (
            command,                          # analysis = команда пользователя
            "",                               # code = пусто (не используется)
            "Обработка в песочнице Gemini...",  # explanation
            output_path,
        )

    # --- Локальный режим (по умолчанию) ---
    # Сбор информации о файлах в потоке, чтобы не блокировать event loop
    file_summaries = await asyncio.gather(*[
        asyncio.to_thread(file_service.get_file_summary, fp)
        for fp in file_paths
    ])

    # Генерация кода через AI с таймаутом и в отдельном потоке
    try:
        async with asyncio.timeout(120):
            result = await asyncio.to_thread(
                ai_service.generate_code,
                user_command=command,
                file_summaries=list(file_summaries),
                input_path=input_path,
                output_path=output_path,
            )
    except asyncio.TimeoutError:
        print("[_process_ai_request] AI не ответил за 120 секунд")
        return None

    return (
        result.get("analysis", ""),
        result.get("code", ""),
        result.get("explanation", ""),
        output_path,
    )


async def _execute_code(code: str, output_path: str, input_path: str | None = None) -> dict:
    """Удалить старый output, выполнить код, вернуть результат."""
    old_output = Path(output_path)
    if old_output.exists():
        old_output.unlink()

    if not input_path:
        return {
            "success": False,
            "stdout": "",
            "stderr": "❌ Внутренняя ошибка: не указан путь к входному файлу.",
            "output_path": output_path,
        }

    if config.EXECUTION_MODE == "assistants":
        # Выполнение в песочнице Gemini (безопаснее, без локального exec)
        execution_result = await gemini_assistant.execute(
            code=code, input_path=input_path, output_path=output_path
        )
    else:
        # Локальное выполнение через изолированный exec()
        execution_result = code_executor.execute(
            code=code, input_path=input_path, output_path=output_path
        )
    return execution_result


async def _send_report(status_msg, analysis: str, explanation: str, execution_result: dict):
    """Сформировать и отправить текстовый отчёт."""

    parts = []

    if execution_result["success"]:
        parts.append("✅ **Обработка завершена успешно!**")
    else:
        parts.append("❌ **Ошибка при выполнении:**")

    stdout = execution_result.get("stdout", "").strip()
    if stdout:
        parts.append(f"\n📤 **Вывод:**\n```\n{stdout[:1000]}\n```")

    stderr = execution_result.get("stderr", "").strip()
    if stderr:
        parts.append(f"\n⚠️ **Логи/Ошибки:**\n```\n{stderr[:1500]}\n```")

    if explanation:
        parts.append(f"\n💡 **Пояснение:**\n{sanitize_for_markdown(explanation)}")

    await status_msg.edit_text("\n".join(parts))


async def _send_result_file(message: Message, user_id: int, output_path: str, execution_result: dict, session: dict):
    """Найти созданный файл и отправить его пользователю."""
    from aiogram.types import FSInputFile
    processed_dir = Path(config.PROCESSED_DIR)
    output_path_obj = Path(output_path)
    found_file = None
    now = time.time()

    if output_path_obj.exists():
        found_file = output_path_obj
    else:
        # Ищем ЛЮБОЙ файл младше 60 секунд
        all_recent = sorted(
            [f for f in processed_dir.iterdir() if f.is_file() and now - f.stat().st_mtime < 60],
            key=os.path.getmtime,
            reverse=True,
        )
        if all_recent:
            found_file = all_recent[0]

    if found_file:
        file_service.save_processed(found_file, user_id)
        display_name = found_file.name
        parts = display_name.split("_", 1)
        if len(parts) > 1 and len(parts[0]) == 32:
            display_name = parts[1]

        # Проверка: если расширение бинарное, но файл на самом деле текст —
        # меняем расширение, чтобы файл открывался
        BINARY_EXTENSIONS = {".xlsx", ".xls", ".ods", ".docx", ".doc"}
        if found_file.suffix.lower() in BINARY_EXTENSIONS:
            try:
                # Читаем первые 100 байт — если не ZIP/OLE2, значит это текст
                header = found_file.read_bytes()[:4]
                is_actually_text = header not in (
                    b"\x50\x4B\x03\x04",  # ZIP (xlsx, docx)
                    b"\xD0\xCF\x11\xE0",  # OLE2 (xls, doc)
                )
                if is_actually_text:
                    txt_name = Path(display_name).with_suffix(".txt").name
                    display_name = txt_name
            except OSError:
                pass

        await message.answer_document(
            document=FSInputFile(path=str(found_file), filename=display_name),
            caption=f"✅ Результат обработки: {display_name}",
        )
        session["file_paths"].append(str(found_file))
    else:
        stdout_text = execution_result.get("stdout", "").strip()
        if stdout_text:
            txt_path = Path(config.PROCESSED_DIR) / f"output_{int(now)}.txt"
            txt_path.write_text(stdout_text, encoding="utf-8")
            await message.answer_document(
                document=FSInputFile(path=str(txt_path), filename="output.txt"),
                caption="📄 Результат выполнения (вывод программы)",
            )
            session["file_paths"].append(str(txt_path))
        else:
            await message.answer("⚠️ Файл результата не найден, но код выполнен успешно.")


async def _process_action(
    msg: Message,
    user_id: int,
    command: str,
    require_confirm: bool = True,
):
    """Обработать команду через AI: сгенерировать код, опционально подтвердить и выполнить."""
    session = _get_session(user_id)
    file_paths = session["file_paths"]

    if not file_paths:
        await msg.answer("📭 Нет загруженных файлов.")
        return

    try:
        ai_result = await _process_ai_request(user_id, command, file_paths)
        if ai_result is None:
            await msg.answer("❌ Не удалось получить ответ от AI. Попробуйте переформулировать запрос.")
            return

        analysis, code, explanation, output_path = ai_result
        session["last_code"] = code

        if not code and config.EXECUTION_MODE != "assistants":
            await msg.answer(
                f" **Ответ:**\n{sanitize_for_markdown(explanation)}"
            )
            return

        if config.EXECUTION_MODE == "assistants":
            # Gemini сама генерирует и выполняет код — require_confirm игнорируется
            execution_result = await _execute_code(code, output_path, input_path=file_paths[0] if file_paths else None)
            await _send_report(msg, analysis, explanation, execution_result)
            await _send_result_file(msg, user_id, output_path, execution_result, session)
            return

        if require_confirm:
            # Запрашиваем подтверждение
            session["pending_code"] = {
                "analysis": analysis,
                "code": code,
                "explanation": explanation,
                "output_path": output_path,
                "input_path": file_paths[0] if file_paths else None,
            }
            await msg.answer(
                f" **Пояснение:**\n{sanitize_for_markdown(explanation)}\n\n"
                f"💻 Выполнить сгенерированный код?",
                reply_markup=confirmation_keyboard(),
            )
        else:
            # Сразу выполняем (для конвертации форматов)
            execution_result = await _execute_code(code, output_path, input_path=file_paths[0] if file_paths else None)
            await _send_report(msg, analysis, explanation, execution_result)
            await _send_result_file(msg, user_id, output_path, execution_result, session)

    except Exception as e:
        await msg.answer(f"❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()


@router.message()
async def handle_unknown(message: Message):
    """Обработчик неизвестных типов сообщений."""
    await message.answer(
        "❓ Я понимаю только текст и файлы.\n"
        "Отправьте файл для обработки или напишите команду.\n"
        "Используйте /help для справки."
    )
