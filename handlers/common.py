"""
Общие состояние и хелперы для всех роутеров.
"""

import asyncio
import logging
import os
import time
from pathlib import Path

from aiogram.types import Message

from config import config
from services.ai_service import ai_service
from services.code_executor import code_executor
from services.file_service import file_service
from services.gemini_assistant import gemini_assistant
from utils.helpers import sanitize_for_markdown

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
        stale_uids = [
            uid for uid, stamps in times_dict.items()
            if not stamps or (now - max(stamps)) > 3600
        ]
        for uid in stale_uids:
            del times_dict[uid]
    stale_sessions = [
        uid for uid, sess in user_sessions.items()
        if not sess.get("file_paths") and not sess.get("last_code")
        and uid not in _known_users
    ]
    for uid in stale_sessions:
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
            "history": [],
        }
    return user_sessions[user_id]


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


# === AI Pipeline ===

async def _process_ai_request(user_id: int, command: str, file_paths: list[str]) -> tuple | None:
    """Собрать контекст → отправить в AI → получить (analysis, code, explanation, output_path)."""

    primary_file = file_paths[0]
    input_path = primary_file

    primary_name = Path(primary_file).name
    parts = primary_name.split("_", 1)
    clean_name = parts[1] if len(parts) > 1 else primary_name
    base, ext = os.path.splitext(clean_name)
    output_name = f"{base}_result{ext}"
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


async def _execute_code(code: str, output_path: str, input_path: str | None = None,
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
        execution_result = await gemini_assistant.execute(
            code=code, input_path=input_path, output_path=output_path,
            user_command=user_command,
        )
    else:
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


async def _send_result_file(message: Message, user_id: int, output_path: str, execution_result: dict, session: dict):
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
        # Если файл не создан — отправляем stdout как текст
        stdout = execution_result.get("stdout", "").strip()
        if stdout:
            await message.answer(
                f"⚠️ Файл результата не создан, но получен вывод:\n\n```\n{stdout[:2000]}\n```"
            )
        else:
            await message.answer(
                "⚠️ Файл результата не создан. Проверьте код и попробуйте снова."
            )


async def _process_action(msg, user_id: str, command: str, require_confirm: bool = True):
    """Универсальная обработка действия: AI → код → выполнение/подтверждение."""
    from utils.keyboards import confirmation_keyboard

    session = _get_session(user_id)
    file_paths = session.get("file_paths", [])
    if not file_paths:
        await msg.answer("📤 Сначала загрузите файл.")
        return

    status_msg = await msg.answer("⏳ Анализирую ваш запрос...")

    try:
        ai_result = await _process_ai_request(user_id, command, file_paths)
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
            execution_result = await _execute_code(
                code, output_path,
                input_path=file_paths[0] if file_paths else None,
                user_command=command,
            )
            await _send_report(status_msg, analysis, explanation, execution_result)
            await _send_result_file(msg, user_id, output_path, execution_result, session)
            return

        if not require_confirm:
            # Выполняем сразу без подтверждения
            await status_msg.edit_text("⏳ Выполняю код...")
            execution_result = await _execute_code(
                code, output_path,
                input_path=file_paths[0] if file_paths else None,
            )
            await _send_report(status_msg, analysis, explanation, execution_result)
            await _send_result_file(msg, user_id, output_path, execution_result, session)
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
