"""
Unit-тесты для handlers/text.py.
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture
def mock_message():
    """Создать mock-сообщение."""
    msg = MagicMock()
    msg.from_user.id = 12345
    msg.text = "test command"
    msg.answer = AsyncMock()
    msg.answer_document = AsyncMock()
    return msg


@pytest.fixture(autouse=True)
def clean_sessions():
    """Очищаем сессии перед каждым тестом."""
    from handlers.common import _known_users, _user_request_times, user_sessions
    user_sessions.clear()
    _known_users.clear()
    _user_request_times.clear()
    yield


class TestHandleText:
    """Тесты handle_text."""

    async def test_no_files_prompts_upload(self, mock_message):
        """Без файлов — предложение загрузить."""
        from handlers.text import handle_text
        await handle_text(mock_message)
        mock_message.answer.assert_called_once()
        text = mock_message.answer.call_args[0][0]
        assert "загрузите файл" in text

    @patch("handlers.text.process_action", new_callable=AsyncMock)
    async def test_with_files_calls_process_action(self, mock_process, mock_message):
        """С файлами — вызов process_action."""
        from handlers.common import user_sessions
        user_sessions[12345] = {
            "file_paths": ["/tmp/test.xlsx"],
            "last_code": "",
        }
        from handlers.text import handle_text
        await handle_text(mock_message)
        mock_process.assert_called_once()

    @patch("handlers.text.process_action", new_callable=AsyncMock)
    async def test_with_pending_action_filter(self, mock_process, mock_message):
        """С pending_action типа filter — подстановка шаблона."""
        from handlers.common import user_sessions
        user_sessions[12345] = {
            "file_paths": ["/tmp/test.xlsx"],
            "last_code": "",
            "pending_action": {"type": "filter"},
        }
        mock_message.text = "amount > 100"
        from handlers.text import handle_text
        await handle_text(mock_message)
        call_args = mock_process.call_args[0]
        assert "Отфильтруй" in call_args[2] or "Условие" in call_args[2]

    @patch("handlers.common.time.time")
    async def test_rate_limit_exceeded(self, mock_time, mock_message):
        """Превышение rate-limit — сообщение."""
        from config import config
        from handlers.common import _user_request_times

        mock_time.return_value = 1000000.0
        # Заполняем лимит
        _user_request_times[12345] = [1000000.0] * config.RATE_LIMIT_REQUESTS_PER_MIN

        from handlers.common import user_sessions
        user_sessions[12345] = {
            "file_paths": ["/tmp/test.xlsx"],
            "last_code": "",
        }

        from handlers.text import handle_text
        await handle_text(mock_message)
        mock_message.answer.assert_called_once()
        text = mock_message.answer.call_args[0][0]
        assert "лимит" in text or "много запросов" in text


class TestHandleUnknown:
    """Тесты handle_unknown (fallback)."""

    async def test_unknown_message_type(self):
        """Неподдерживаемый тип сообщения."""
        msg = MagicMock()
        msg.answer = AsyncMock()
        from handlers.text import handle_unknown
        await handle_unknown(msg)
        msg.answer.assert_called_once()
        text = msg.answer.call_args[0][0]
        assert "Отправьте мне файл" in text


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])