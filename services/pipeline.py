"""
AI Pipeline — связующее звено между обработчиками и сервисами.
Содержит всю логику: AI-запрос → генерация кода → выполнение → отправка результата.
"""

import asyncio
import logging
import os
import time
from pathlib import Path

from config import config
from services.ai_service import ai_service
from services.code_executor import code_executor
from services.file_service import file_service
from services.gemini_assistant import gemini_assistant
from services.profiler import Timer
from utils.helpers import sanitize_for_markdown

logger = logging.getLogger(__name__)


# === Константы для определения формата ===
_TARGET_FORMAT_KEYWORDS = {
    ".txt": ["txt", "текстовый формат", "текст"],
    ".csv": ["csv"],
    ".xlsx": ["xlsx", "excel", "эксель", "еxcel"],
    ".xls": ["xls", "старый excel"],
    ".docx": ["docx", "word", "ворд"],
    ".ods": ["ods"],
}


def _detect_target_extension(command: str, source_ext: str) -> str:
    """Определить целевое расширение из текста команды пользователя."""
    cmd_lower = command.lower()
    for ext, keywords in _TARGET_FORMAT_KEYWORDS.items():
        if any(kw in cmd_lower for kw in keywords):
            return ext
    return source_ext


async def process_ai_request(user_id: int, command: str, file_paths: list[str]) -> tuple | None:
    """Собрать контекст → отправить в AI → получить (analysis, code, explanation, output_path)."""

    primary_file = file_paths[0]
    input_path = primary_file

    primary_name = Path(primary_file).name
    parts = primary_name.split("_", 1)
    clean_name = parts[1] if len(parts) > 1 else primary_name
    base, source_ext = os.path.splitext(clean_name)
    # Определяем целевое расширение из команды пользователя
    target_ext = _detect_target_extension(command, source_ext)
    output_name = f"{base}_result{target_ext}"
    # Изоляция по user_id
    user_output_dir = Path(config.PROCESSED_DIR) / str(user_id)
    user_output_dir.mkdir(parents=True, exist_ok=True)
    output_path = str(user_output_dir / output_name)

    if config.EXECUTION_MODE == "assistants":
        return (command, "", "Обработка через Gemini (код выполняется локально)...", output_path)

    # Локальный режим
    file_summaries = await asyncio.gather(*[
        asyncio.to_thread(file_service.get_file_summary, fp)
        for fp in file_paths
    ])

    try:
        async with asyncio.timeout(120):
            with Timer('ai.generate_code', log_slow=2.0):
                result = await asyncio.to_thread(
                    ai_service.generate_code,
                    user_command=command,
                    file_summaries=list(file_summaries),
                    input_path=input_path,
                    output_path=output_path,
                )
    except TimeoutError:
        logger.warning("AI не ответил за 120 секунд")
        return None

    if result is None:
        return None

    return (
        result.get("analysis", ""),
        result.get("code", ""),
        result.get("explanation", ""),
        output_path,
    )


async def execute_code(code: str, output_path: str, input_path: str | None = None,
                       user_command: str = "") -> dict:
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
        with Timer('gemini.execute', log_slow=5.0):
            execution_result = await gemini_assistant.execute(
                code=code, input_path=input_path, output_path=output_path,
                user_command=user_command,
            )
    else:
        with Timer('code_executor.execute', log_slow=5.0):
            execution_result = await asyncio.to_thread(
                code_executor.execute,
                code=code,
                input_path=input_path,
                output_path=output_path,
            )
    return execution_result


async def send_report(status_msg, analysis: str, explanation: str, execution_result: dict):
    """Сформировать и отправить текстовый отчёт."""

    parts = []

    if execution_result["success"]:
        parts.append("✅ **Обработка завершена успешно!**")
    else:
        parts.append("❌ **Ошибка при выполнении:**")

    stdout = execution_result.get("stdout", "").strip()
    if stdout:
        safe_stdout = stdout[:1000].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        parts.append(f"\n📤 **Вывод:**\n```\n{safe_stdout}\n```")

    stderr = execution_result.get("stderr", "").strip()
    if stderr:
        safe_stderr = stderr[:1500].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        parts.append(f"\n⚠️ **Логи/Ошибки:**\n```\n{safe_stderr}\n```")

    if explanation:
        parts.append(f"\n💡 **Пояснение:**\n{sanitize_for_markdown(explanation)}")

    try:
        await status_msg.edit_text("\n".join(parts), parse_mode="Markdown")
    except Exception:
        try:
            await status_msg.edit_text("\n".join(parts), parse_mode=None)
        except Exception:
            pass


async def send_result_file(message, user_id: int, output_path: str, execution_result: dict, session: dict):
    """Найти созданный файл и отправить его пользователю."""
    from aiogram.types import FSInputFile
    output_path_obj = Path(output_path)
    now = time.time()

    if output_path_obj.exists():
        found_file = output_path_obj
    else:
        user_processed = Path(config.PROCESSED_DIR) / str(user_id)
        if user_processed.exists():
            recent = sorted(
                [f for f in user_processed.iterdir() if f.is_file() and now - f.stat().st_mtime < 10],
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            found_file = recent[0] if recent else None
        else:
            found_file = None

    if found_file:
        file_service.save_processed(found_file, user_id)
        display_name = found_file.name
        parts = display_name.split("_", 1)
        if len(parts) > 1 and len(parts[0]) == 32:
            display_name = parts[1]

        # Определяем реальный тип файла (защита от неверного расширения)
        text_extensions = {".txt", ".csv", ".json", ".xml"}
        content = found_file.read_bytes()[:512]
        likely_binary = b"\x00" in content

        if found_file.suffix in text_extensions and likely_binary:
            logger.info("Формат изменён: был %s, создан %s", found_file.suffix, ".txt")
            new_path = found_file.with_suffix(".txt")
            found_file.rename(new_path)
            found_file = new_path
            display_name = found_file.name

        try:
            input_file = FSInputFile(str(found_file))
            await message.answer_document(
                input_file,
                caption=f"📎 Готовый файл: `{display_name}`",
            )
        except Exception as e:
            logger.error("Ошибка отправки файла: %s", e)
            await message.answer("❌ Не удалось отправить файл. Попробуйте ещё раз.")
    else:
        stdout = execution_result.get("stdout", "").strip()
        if stdout:
            await message.answer(
                f"⚠️ Файл результата не создан, но получен вывод:\n\n```\n{stdout[:2000]}\n```"
            )
        else:
            await message.answer(
                "⚠️ Файл результата не создан. Проверьте код и попробуйте снова."
            )


async def process_action(msg, user_id: str, command: str, require_confirm: bool = True):
    """Универсальная обработка действия: AI → код → выполнение/подтверждение."""
    from handlers.common import _get_session
    from utils.keyboards import confirmation_keyboard

    session = _get_session(user_id)
    file_paths = session.get("file_paths", [])
    if not file_paths:
        await msg.answer("📤 Сначала загрузите файл.")
        return

    status_msg = await msg.answer("⏳ Анализирую ваш запрос...")

    try:
        ai_result = await process_ai_request(user_id, command, file_paths)
        if ai_result is None:
            await status_msg.edit_text("❌ Не удалось получить ответ от AI. Попробуйте переформулировать запрос.")
            return

        analysis, code, explanation, output_path = ai_result
        session["last_code"] = code

        if not code and config.EXECUTION_MODE != "assistants":
            await status_msg.edit_text(f"💡 **Ответ:**\n{sanitize_for_markdown(explanation)}")
            return

        if config.EXECUTION_MODE == "assistants":
            await status_msg.edit_text("⏳ Gemini генерирует код...")
            execution_result = await execute_code(
                code, output_path,
                input_path=file_paths[0] if file_paths else None,
                user_command=command,
            )
            await send_report(status_msg, analysis, explanation, execution_result)
            await send_result_file(msg, user_id, output_path, execution_result, session)
            return

        if not require_confirm:
            # Выполняем сразу без подтверждения
            await status_msg.edit_text("⏳ Выполняю код...")
            execution_result = await execute_code(
                code, output_path,
                input_path=file_paths[0] if file_paths else None,
            )
            await send_report(status_msg, analysis, explanation, execution_result)
            await send_result_file(msg, user_id, output_path, execution_result, session)
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
            f"💡 **Пояснение:**\n{sanitize_for_markdown(explanation)}\n\n"
            f"💻 Выполнить сгенерированный код?",
            reply_markup=confirmation_keyboard(),
        )

    except Exception:
        import traceback
        traceback.print_exc()
        await status_msg.edit_text("❌ Внутренняя ошибка при обработке запроса. Попробуйте позже.")