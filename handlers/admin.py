"""
Админские команды: /admin, /stats, /broadcast.
"""

import asyncio
import html
import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from config import config
from handlers.common import (
    _is_admin,
    _known_users,
    user_sessions,
)

logger = logging.getLogger(__name__)

router = Router()


@router.message(Command("admin"))
async def cmd_admin(message: Message):
    """Панель администратора (только для ADMIN_IDS)."""
    user_id = message.from_user.id
    if not _is_admin(user_id):
        await message.answer("⛔ У вас нет прав администратора.")
        return

    admin_id = message.from_user.id
    active_users = len(user_sessions)
    total_users = len(_known_users)
    total_files = sum(len(s.get("file_paths", [])) for s in user_sessions.values())

    admin_text = (
        f"<b>🛠 Панель администратора</b>\n\n"
        f"👤 Ваш ID: <code>{admin_id}</code>\n"
        f"👥 Всего известных пользователей: <code>{total_users}</code>\n"
        f"👥 Активных сессий: <code>{active_users}</code>\n"
        f"📎 Всего файлов в сессиях: <code>{total_files}</code>\n\n"
        f"<b>Команды:</b>\n"
        f"<code>/broadcast &lt;текст&gt;</code> — отправить сообщение всем известным пользователям\n"
        f"<code>/stats</code> — полная статистика бота"
    )
    await message.answer(admin_text, parse_mode="HTML")


@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message):
    """Отправить сообщение всем известным пользователям (только админ)."""
    user_id = message.from_user.id
    if not _is_admin(user_id):
        await message.answer("⛔ У вас нет прав администратора.")
        return

    text = message.text.replace("/broadcast", "", 1).strip()
    if not text:
        await message.answer(
            "❌ Укажите текст для рассылки.\nПример: <code>/broadcast Всем привет!</code>",
            parse_mode="HTML",
        )
        return

    safe_text = html.escape(text)

    sent = 0
    errors = 0
    for uid in list(_known_users):
        try:
            await message.bot.send_message(
                chat_id=uid,
                text=f"📢 <b>Сообщение от администратора:</b>\n\n{safe_text}",
                parse_mode="HTML",
            )
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            errors += 1

    await message.answer(
        f"✅ Сообщение отправлено <code>{sent}</code> пользователям. Ошибок: <code>{errors}</code>.",
        parse_mode="HTML",
    )


@router.message(Command("stats"))
async def cmd_stats(message: Message):
    """Статистика бота (только админ)."""
    user_id = message.from_user.id
    if not _is_admin(user_id):
        await message.answer("⛔ У вас нет прав администратора.")
        return

    total_users = len(user_sessions)
    total_files = 0
    total_history = 0
    for s in user_sessions.values():
        total_files += len(s.get("file_paths", []))
        total_history += len(s.get("history", []))

    stats_text = (
        f"<b>📊 Статистика бота</b>\n\n"
        f"👥 Пользователей в сессиях: <code>{total_users}</code>\n"
        f"📎 Всего файлов: <code>{total_files}</code>\n"
        f"💬 Сообщений в истории: <code>{total_history}</code>\n"
        f"⚙️ Провайдер: <code>{config.AI_PROVIDER}</code>\n"
        f"🔧 Режим exec: <code>{config.EXECUTION_MODE}</code>"
    )
    await message.answer(stats_text, parse_mode="HTML")
