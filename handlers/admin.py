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
from services.profiler import get_stats, reset_stats

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

    # ✅ Параллельная отправка с семафором (не больше 20 одновременных)
    sem = asyncio.Semaphore(20)

    async def _send_one(uid: int) -> bool:
        async with sem:
            try:
                await message.bot.send_message(
                    chat_id=uid,
                    text=f"📢 <b>Сообщение от администратора:</b>\n\n{safe_text}",
                    parse_mode="HTML",
                )
                return True
            except Exception:
                return False

    results = await asyncio.gather(*[_send_one(uid) for uid in list(_known_users)])
    sent = sum(1 for r in results if r)
    errors = len(results) - sent

    await message.answer(
        f"✅ Сообщение отправлено <code>{sent}</code> пользователям. Ошибок: <code>{errors}</code>.",
        parse_mode="HTML",
    )


@router.message(Command("perf"))
async def cmd_perf(message: Message):
    """Статистика производительности (только админ)."""
    user_id = message.from_user.id
    if not _is_admin(user_id):
        await message.answer("⛔ У вас нет прав администратора.")
        return

    stats = get_stats()
    if not stats:
        await message.answer("📊 Нет данных производительности.")
        return

    lines = ["<b>📊 Производительность</b>\n"]
    for label, metric in stats.items():
        lines.append(
            f"<code>{label}</code>\n"
            f"  Вызовов: {metric['count']} | "
            f"Среднее: {metric['mean']:.2f}с | "
            f"Медиана: {metric['median']:.2f}с\n"
            f"  Мин: {metric['min']:.2f}с | "
            f"Макс: {metric['max']:.2f}с | "
            f"P95: {metric['p95']:.2f}с\n"
        )
    lines.append("\n<code>/perf_reset</code> — сбросить статистику")

    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("perf_reset"))
async def cmd_perf_reset(message: Message):
    """Сбросить статистику производительности (только админ)."""
    if not _is_admin(message.from_user.id):
        await message.answer("⛔ У вас нет прав администратора.")
        return
    reset_stats()
    await message.answer("✅ Статистика производительности сброшена.")


@router.message(Command("stats"))
async def cmd_stats(message: Message):
    """Статистика бота (только админ)."""
    user_id = message.from_user.id
    if not _is_admin(user_id):
        await message.answer("⛔ У вас нет прав администратора.")
        return

    total_users = len(user_sessions)
    total_files = 0
    for s in user_sessions.values():
        total_files += len(s.get("file_paths", []))

    stats_text = (
        f"<b>📊 Статистика бота</b>\n\n"
        f"👥 Пользователей в сессиях: <code>{total_users}</code>\n"
        f"📎 Всего файлов: <code>{total_files}</code>\n"
        f"⚙️ Провайдер: <code>{config.AI_PROVIDER}</code>\n"
        f"🔧 Режим exec: <code>{config.EXECUTION_MODE}</code>"
    )
    await message.answer(stats_text, parse_mode="HTML")
