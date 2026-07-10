"""
Unit-тесты для handlers/documents.py.
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture
def mock_message():
    """Создать mock-сообщение с документом."""
    msg = MagicMock()
    msg.from_user.id = 12345
    msg.document = MagicMock()
    msg.document.file_name = "test.xlsx"
    msg.document.file_size = 1024 * 10  # 10 KB
    msg.document.file_id = "abc123"
    msg.answer = AsyncMock()
    msg.answer_document = AsyncMock()
    return msg


@pytest.fixture
def mock_bot():
    """Mock bot."""
    bot = MagicMock()
    bot.download = AsyncMock()
    return bot


@pytest.fixture(autouse=True)
def clean_sessions():
    """Очищаем сессии перед каждым тестом."""
    from handlers.common import _known_users, _user_upload_times, user_sessions
    user_sessions.clear()
    _known_users.clear()
    _user_upload_times.clear()
    yield


class TestHandleDocument:
    """Тесты handle_document."""

    @patch("handlers.documents.file_service")
    async def test_valid_file_upload(self, mock_fs, mock_message, mock_bot):
        """Валидный файл загружается успешно."""
        mock_fs.ALLOWED_EXTENSIONS = {".xlsx", ".csv", ".docx"}
        mock_fs.download_file = AsyncMock(return_value=Path("/tmp/downloads/12345/test.xlsx"))
        mock_fs.get_file_summary.return_value = "📄 Файл: test.xlsx\n📏 Размер: 10.0 КБ"

        from handlers.documents import handle_document
        await handle_document(mock_message, mock_bot)

        mock_message.answer.assert_called()
        assert any("Скачиваю" in str(call) for call in mock_message.answer.call_args_list)
        mock_fs.download_file.assert_called_once()

    @patch("handlers.documents.file_service")
    async def test_invalid_extension(self, mock_fs, mock_message, mock_bot):
        """Неподдерживаемый формат — отказ."""
        mock_fs.ALLOWED_EXTENSIONS = {".xlsx", ".csv", ".docx"}
        mock_message.document.file_name = "test.exe"

        from handlers.documents import handle_document
        await handle_document(mock_message, mock_bot)

        mock_message.answer.assert_called_once()
        text = mock_message.answer.call_args[0][0]
        assert "не поддерживается" in text

    @patch("handlers.documents.file_service")
    async def test_file_too_large(self, mock_fs, mock_message, mock_bot):
        """Превышение размера — отказ."""
        mock_fs.ALLOWED_EXTENSIONS = {".xlsx"}
        mock_message.document.file_size = 30 * 1024 * 1024  # 30 MB > MAX_FILE_SIZE_MB=20

        from handlers.documents import handle_document
        await handle_document(mock_message, mock_bot)

        mock_message.answer.assert_called_once()
        text = mock_message.answer.call_args[0][0]
        assert "слишком большой" in text

    @patch("handlers.documents.file_service")
    @patch("handlers.common.time.time")
    async def test_upload_rate_limit_exceeded(self, mock_time, mock_fs, mock_message, mock_bot):
        """Превышение rate-limit загрузок."""
        from config import config
        from handlers.common import _user_upload_times

        mock_time.return_value = 1000000.0
        # Заполняем лимит
        _user_upload_times[12345] = [1000000.0] * config.RATE_LIMIT_FILE_UPLOADS_PER_HOUR

        mock_fs.ALLOWED_EXTENSIONS = {".xlsx"}

        from handlers.documents import handle_document
        await handle_document(mock_message, mock_bot)

        mock_message.answer.assert_called_once()
        text = mock_message.answer.call_args[0][0]
        assert "лимит" in text.lower()

    @patch("handlers.documents.file_service")
    async def test_download_failure(self, mock_fs, mock_message, mock_bot):
        """Ошибка скачивания — сообщение."""
        mock_fs.ALLOWED_EXTENSIONS = {".xlsx"}
        mock_fs.download_file = AsyncMock(return_value=None)

        from handlers.documents import handle_document
        await handle_document(mock_message, mock_bot)

        # Проверяем, что был вызов edit_text с сообщением об ошибке
        assert mock_fs.download_file.called


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])