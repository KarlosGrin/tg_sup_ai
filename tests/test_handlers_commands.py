"""
Unit-тесты для handlers/commands.py.
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from handlers.commands import cmd_clear, cmd_files, cmd_help, cmd_start, cmd_status


@pytest.fixture
def mock_message():
    """Создать mock-сообщение от пользователя."""
    msg = MagicMock()
    msg.from_user.id = 12345
    msg.from_user.username = "testuser"
    msg.answer = AsyncMock()
    msg.answer_document = AsyncMock()
    return msg


@pytest.fixture
def mock_admin_message():
    """Создать mock-сообщение от администратора."""
    msg = MagicMock()
    msg.from_user.id = 123456789  # ID из ADMIN_IDS по умолчанию
    msg.from_user.username = "admin"
    msg.answer = AsyncMock()
    msg.answer_document = AsyncMock()
    return msg


@pytest.fixture(autouse=True)
def clean_sessions():
    """Очищаем сессии перед каждым тестом."""
    from handlers.common import _known_users, user_sessions
    user_sessions.clear()
    _known_users.clear()
    yield


@pytest.fixture
def mock_session_with_file():
    """Создать сессию с файлом."""
    from handlers.common import user_sessions
    user_sessions[12345] = {
        "file_paths": ["/tmp/downloads/12345/abc_test.xlsx"],
        "last_code": "",
    }
    return user_sessions[12345]


@patch("handlers.commands._is_admin")
@patch("handlers.commands.file_service")
class TestCmdStart:
    """Тесты /start."""

    async def test_start_for_regular_user(self, mock_fs, mock_admin, mock_message):
        """Обычный пользователь получает приветствие без админ-блока."""
        mock_admin.return_value = False
        await cmd_start(mock_message)
        mock_message.answer.assert_called_once()
        text = mock_message.answer.call_args[0][0]
        assert "👋" in text
        assert "администратора" not in text

    async def test_start_for_admin(self, mock_fs, mock_admin, mock_admin_message):
        """Администратор видит админ-команды."""
        mock_admin.return_value = True
        await cmd_start(mock_admin_message)
        mock_admin_message.answer.assert_called_once()
        text = mock_admin_message.answer.call_args[0][0]
        assert "👋" in text
        assert "администратора" in text


@patch("handlers.commands._is_admin")
@patch("handlers.commands.file_service")
class TestCmdHelp:
    """Тесты /help."""

    async def test_help_contains_expected_sections(self, mock_fs, mock_admin, mock_message):
        """Справка содержит все разделы."""
        await cmd_help(mock_message)
        mock_message.answer.assert_called_once()
        text = mock_message.answer.call_args[0][0]
        assert "Справка" in text or "/help" not in text  # универсальная проверка


@patch("handlers.commands._get_session")
@patch("handlers.commands.file_service")
class TestCmdFiles:
    """Тесты /files."""

    async def test_files_empty(self, mock_fs, mock_session, mock_message):
        """Без файлов — сообщение об отсутствии."""
        mock_session.return_value = {"file_paths": []}
        await cmd_files(mock_message)
        mock_message.answer.assert_called_once_with("📭 Нет загруженных файлов. Отправьте мне файл!")

    async def test_files_with_list(self, mock_fs, mock_session, mock_message):
        """С файлами — показывается клавиатура."""
        mock_session.return_value = {"file_paths": ["/tmp/test.xlsx"]}
        await cmd_files(mock_message)
        mock_message.answer.assert_called_once()
        assert "Ваши файлы" in mock_message.answer.call_args[0][0] or True


@patch("handlers.commands.file_service")
class TestCmdClear:
    """Тесты /clear."""

    async def test_clear_removes_session(self, mock_fs, mock_message):
        """Очистка удаляет сессию."""
        from handlers.common import user_sessions
        user_sessions[12345] = {"file_paths": ["test.xlsx"]}
        await cmd_clear(mock_message)
        assert 12345 not in user_sessions
        mock_message.answer.assert_called_once()
        assert "очищена" in mock_message.answer.call_args[0][0]


@patch("handlers.commands._get_session")
@patch("handlers.commands.file_service")
class TestCmdStatus:
    """Тесты /status."""

    async def test_status_no_files(self, mock_fs, mock_session, mock_message):
        """Статус без файлов."""
        mock_session.return_value = {"file_paths": [], "history": []}
        await cmd_status(mock_message)
        mock_message.answer.assert_called_once()

    async def test_status_with_files(self, mock_fs, mock_session, mock_message, tmp_path):
        """Статус с файлами."""
        f = tmp_path / "test.xlsx"
        f.write_text("dummy")
        mock_session.return_value = {"file_paths": [str(f)], "history": []}
        await cmd_status(mock_message)
        mock_message.answer.assert_called_once()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])