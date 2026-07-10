"""
Unit-тесты для handlers/admin.py.
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture
def mock_admin_msg():
    """Mock-сообщение от администратора (с моком _is_admin)."""
    msg = MagicMock()
    msg.from_user.id = 12345
    msg.text = "/admin"
    msg.bot = MagicMock()
    msg.bot.send_message = AsyncMock()
    msg.answer = AsyncMock()
    return msg


@pytest.fixture
def mock_user_msg():
    """Mock-сообщение от обычного пользователя."""
    msg = MagicMock()
    msg.from_user.id = 99999
    msg.text = "/admin"
    msg.bot = MagicMock()
    msg.bot.send_message = AsyncMock()
    msg.answer = AsyncMock()
    return msg


@pytest.fixture(autouse=True)
def _clean_known_users():
    """Очищаем известных пользователей."""
    from handlers.common import _known_users, user_sessions
    _known_users.clear()
    user_sessions.clear()
    yield


class TestCmdAdmin:
    """Тесты /admin."""

    @patch("handlers.admin._is_admin", return_value=True)
    async def test_admin_panel_allowed(self, mock_is_admin, mock_admin_msg):
        """Администратор видит панель."""
        from handlers.admin import cmd_admin
        await cmd_admin(mock_admin_msg)
        mock_admin_msg.answer.assert_called_once()
        text = mock_admin_msg.answer.call_args[0][0]
        assert "Панель администратора" in text

    async def test_admin_panel_denied(self, mock_user_msg):
        """Обычный пользователь получает отказ."""
        from handlers.admin import cmd_admin
        await cmd_admin(mock_user_msg)
        mock_user_msg.answer.assert_called_once_with(
            "⛔ У вас нет прав администратора."
        )


class TestCmdBroadcast:
    """Тесты /broadcast."""

    async def test_broadcast_denied_for_user(self, mock_user_msg):
        """Обычный пользователь не может делать рассылку."""
        from handlers.admin import cmd_broadcast
        await cmd_broadcast(mock_user_msg)
        mock_user_msg.answer.assert_called_once_with(
            "⛔ У вас нет прав администратора."
        )

    @patch("handlers.admin._is_admin", return_value=True)
    async def test_broadcast_no_text(self, mock_is_admin, mock_admin_msg):
        """Без текста — ошибка."""
        from handlers.admin import cmd_broadcast
        mock_admin_msg.text = "/broadcast"
        await cmd_broadcast(mock_admin_msg)
        mock_admin_msg.answer.assert_called_once()

    @patch("handlers.admin._is_admin", return_value=True)
    async def test_broadcast_sends_to_users(self, mock_is_admin, mock_admin_msg):
        """Рассылка отправляется пользователям."""
        from handlers.admin import cmd_broadcast
        from handlers.common import _known_users
        _known_users.update({111, 222})
        mock_admin_msg.text = "/broadcast Всем привет!"
        mock_admin_msg.bot.send_message = AsyncMock()
        await cmd_broadcast(mock_admin_msg)
        assert mock_admin_msg.bot.send_message.call_count == 2


class TestCmdPerf:
    """Тесты /perf."""

    @patch("handlers.admin._is_admin", return_value=True)
    @patch("handlers.admin.get_stats")
    async def test_perf_no_data(self, mock_stats, mock_is_admin, mock_admin_msg):
        """Без данных — сообщение."""
        from handlers.admin import cmd_perf
        mock_stats.return_value = {}
        await cmd_perf(mock_admin_msg)
        mock_admin_msg.answer.assert_called_once_with(
            "📊 Нет данных производительности."
        )

    @patch("handlers.admin._is_admin", return_value=True)
    @patch("handlers.admin.get_stats")
    async def test_perf_with_data(self, mock_stats, mock_is_admin, mock_admin_msg):
        """С данными — красивый вывод."""
        from handlers.admin import cmd_perf
        mock_stats.return_value = {
            "test_op": {
                "count": 5, "mean": 1.5, "median": 1.0,
                "min": 0.5, "max": 3.0, "p95": 2.5,
            }
        }
        await cmd_perf(mock_admin_msg)
        mock_admin_msg.answer.assert_called_once()
        text = mock_admin_msg.answer.call_args[0][0]
        assert "Производительность" in text

    async def test_perf_denied_for_user(self, mock_user_msg):
        """Обычный пользователь не может смотреть perf."""
        from handlers.admin import cmd_perf
        await cmd_perf(mock_user_msg)
        mock_user_msg.answer.assert_called_once_with(
            "⛔ У вас нет прав администратора."
        )


class TestCmdPerfReset:
    """Тесты /perf_reset."""

    @patch("handlers.admin._is_admin", return_value=True)
    @patch("handlers.admin.reset_stats")
    async def test_perf_reset(self, mock_reset, mock_is_admin, mock_admin_msg):
        """Сброс статистики."""
        from handlers.admin import cmd_perf_reset
        await cmd_perf_reset(mock_admin_msg)
        mock_reset.assert_called_once()
        mock_admin_msg.answer.assert_called_once_with(
            "✅ Статистика производительности сброшена."
        )

    async def test_perf_reset_denied(self, mock_user_msg):
        """Обычный пользователь не может сбросить статистику."""
        from handlers.admin import cmd_perf_reset
        await cmd_perf_reset(mock_user_msg)
        mock_user_msg.answer.assert_called_once_with(
            "⛔ У вас нет прав администратора."
        )


class TestCmdStats:
    """Тесты /stats."""

    @patch("handlers.admin._is_admin", return_value=True)
    async def test_stats_allowed(self, mock_is_admin, mock_admin_msg):
        """Администратор видит статистику."""
        from handlers.admin import cmd_stats
        await cmd_stats(mock_admin_msg)
        mock_admin_msg.answer.assert_called_once()
        text = mock_admin_msg.answer.call_args[0][0]
        assert "Статистика" in text

    async def test_stats_denied(self, mock_user_msg):
        """Обычный пользователь не может смотреть статистику."""
        from handlers.admin import cmd_stats
        await cmd_stats(mock_user_msg)
        mock_user_msg.answer.assert_called_once_with(
            "⛔ У вас нет прав администратора."
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])