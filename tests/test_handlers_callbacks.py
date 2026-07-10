"""
Unit-тесты для handlers/callbacks.py.
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture
def mock_callback():
    """Создать mock callback-запроса."""
    cb = MagicMock()
    cb.from_user.id = 12345
    cb.message = MagicMock()
    cb.message.edit_text = AsyncMock()
    cb.message.answer_document = AsyncMock()
    cb.answer = AsyncMock()
    return cb


@pytest.fixture(autouse=True)
def clean_sessions():
    """Очищаем сессии перед каждым тестом."""
    from handlers.common import _known_users, user_sessions
    user_sessions.clear()
    _known_users.clear()
    yield


class TestCallbackCancel:
    """Тесты отмены."""

    async def test_cancel_removes_pending(self, mock_callback):
        """Отмена удаляет pending_action и pending_code."""
        from handlers.common import user_sessions
        user_sessions[12345] = {
            "file_paths": [],
            "pending_action": {"type": "filter"},
            "pending_code": {"code": "print(1)"},
        }
        from handlers.callbacks import callback_cancel
        await callback_cancel(mock_callback)
        session = user_sessions[12345]
        assert "pending_action" not in session
        assert "pending_code" not in session
        mock_callback.message.edit_text.assert_called_once_with("❌ Действие отменено.")
        mock_callback.answer.assert_called_once()


class TestCallbackFileSelect:
    """Тесты выбора файла."""

    async def test_file_select_valid(self, mock_callback):
        """Корректный выбор файла."""
        from handlers.common import user_sessions
        user_sessions[12345] = {
            "file_paths": ["/tmp/downloads/12345/abc_test.xlsx"],
        }
        mock_callback.data = "file_select:0"
        from handlers.callbacks import callback_file_select
        await callback_file_select(mock_callback)
        mock_callback.message.edit_text.assert_called_once()
        mock_callback.answer.assert_called_once()

    async def test_file_select_invalid_index(self, mock_callback):
        """Некорректный индекс."""
        from handlers.common import user_sessions
        user_sessions[12345] = {
            "file_paths": ["/tmp/test.xlsx"],
        }
        mock_callback.data = "file_select:5"
        from handlers.callbacks import callback_file_select
        await callback_file_select(mock_callback)
        mock_callback.answer.assert_called_once_with("❌ Файл не найден.", show_alert=True)


class TestCallbackAction:
    """Тесты быстрых действий."""

    async def test_action_filter_sets_pending(self, mock_callback):
        """Действие filter устанавливает pending_action."""
        from handlers.common import user_sessions
        user_sessions[12345] = {
            "file_paths": ["/tmp/test.xlsx"],
            "active_file_index": 0,
        }
        mock_callback.data = "action:filter"
        from handlers.callbacks import callback_action
        await callback_action(mock_callback)
        session = user_sessions[12345]
        assert session.get("pending_action") == {"type": "filter"}

    async def test_action_chart_sets_pending(self, mock_callback):
        """Действие chart устанавливает pending_action."""
        from handlers.common import user_sessions
        user_sessions[12345] = {
            "file_paths": ["/tmp/test.xlsx"],
            "active_file_index": 0,
        }
        mock_callback.data = "action:chart"
        from handlers.callbacks import callback_action
        await callback_action(mock_callback)
        session = user_sessions[12345]
        assert session.get("pending_action") == {"type": "chart"}

    async def test_action_pivot_sets_pending(self, mock_callback):
        """Действие pivot устанавливает pending_action."""
        from handlers.common import user_sessions
        user_sessions[12345] = {
            "file_paths": ["/tmp/test.xlsx"],
            "active_file_index": 0,
        }
        mock_callback.data = "action:pivot"
        from handlers.callbacks import callback_action
        await callback_action(mock_callback)
        session = user_sessions[12345]
        assert session.get("pending_action") == {"type": "pivot"}

    async def test_action_no_files(self, mock_callback):
        """Без файлов — сообщение."""
        from handlers.common import user_sessions
        user_sessions[12345] = {"file_paths": []}
        mock_callback.data = "action:filter"
        from handlers.callbacks import callback_action
        await callback_action(mock_callback)
        mock_callback.message.edit_text.assert_called_once_with("📤 Сначала загрузите файл.")

    @patch("handlers.callbacks.process_action", new_callable=AsyncMock)
    async def test_action_save_docx(self, mock_process, mock_callback):
        """Сохранение в DOCX."""
        from handlers.common import user_sessions
        user_sessions[12345] = {
            "file_paths": ["/tmp/test.xlsx"],
            "active_file_index": 0,
        }
        mock_callback.data = "action:save_docx"
        from handlers.callbacks import callback_action
        await callback_action(mock_callback)
        mock_process.assert_called_once()

    @patch("handlers.callbacks.process_action", new_callable=AsyncMock)
    async def test_action_save_xlsx(self, mock_process, mock_callback):
        """Сохранение в XLSX."""
        from handlers.common import user_sessions
        user_sessions[12345] = {
            "file_paths": ["/tmp/test.xlsx"],
            "active_file_index": 0,
        }
        mock_callback.data = "action:save_xlsx"
        from handlers.callbacks import callback_action
        await callback_action(mock_callback)
        mock_process.assert_called_once()


class TestCallbackConfirm:
    """Тесты подтверждения кода."""

    async def test_confirm_no_pending(self, mock_callback):
        """Без ожидающего кода — сообщение об истечении."""
        from handlers.common import user_sessions
        user_sessions[12345] = {"file_paths": []}
        mock_callback.data = "confirm:yes"
        from handlers.callbacks import callback_confirm
        await callback_confirm(mock_callback)
        mock_callback.message.edit_text.assert_called_once_with(
            "⏳ Время ожидания истекло. Отправьте запрос заново."
        )

    async def test_confirm_cancel(self, mock_callback):
        """Отмена выполнения кода."""
        from handlers.common import user_sessions
        user_sessions[12345] = {
            "file_paths": [],
            "pending_code": {"code": "print(1)", "output_path": "/tmp/out.txt"},
        }
        mock_callback.data = "confirm:no"
        from handlers.callbacks import callback_confirm
        await callback_confirm(mock_callback)
        mock_callback.message.edit_text.assert_called_once_with(
            "❌ Выполнение кода отменено."
        )

    @patch("handlers.callbacks.execute_code", new_callable=AsyncMock)
    @patch("handlers.callbacks.send_report", new_callable=AsyncMock)
    @patch("handlers.callbacks.send_result_file", new_callable=AsyncMock)
    async def test_confirm_execute(self, mock_send_file, mock_report, mock_exec, mock_callback):
        """Подтверждение выполнения кода."""
        from handlers.common import user_sessions
        user_sessions[12345] = {
            "file_paths": ["/tmp/test.xlsx"],
            "pending_code": {
                "code": "print(1)",
                "output_path": "/tmp/out.txt",
                "explanation": "test explanation",
                "analysis": "test analysis",
                "input_path": "/tmp/test.xlsx",
            },
        }
        mock_exec.return_value = {"success": True, "stdout": "1", "stderr": ""}
        mock_callback.data = "confirm:yes"
        from handlers.callbacks import callback_confirm
        await callback_confirm(mock_callback)
        mock_exec.assert_called_once()
        mock_report.assert_called_once()
        mock_send_file.assert_called_once()


class TestCallbackFormat:
    """Тесты конвертации форматов."""

    @patch("handlers.callbacks.process_action", new_callable=AsyncMock)
    async def test_format_conversion(self, mock_process, mock_callback):
        """Конвертация в другой формат."""
        from handlers.common import user_sessions
        user_sessions[12345] = {
            "file_paths": ["/tmp/test.xlsx"],
            "active_file_index": 0,
        }
        mock_callback.data = "format:csv"
        from handlers.callbacks import callback_format
        await callback_format(mock_callback)
        mock_process.assert_called_once()

    async def test_format_same_extension(self, mock_callback):
        """Конвертация в тот же формат — предупреждение."""
        from handlers.common import user_sessions
        user_sessions[12345] = {
            "file_paths": ["/tmp/test.csv"],
            "active_file_index": 0,
        }
        mock_callback.data = "format:csv"
        from handlers.callbacks import callback_format
        await callback_format(mock_callback)
        mock_callback.message.edit_text.assert_called_once_with(
            "⚠️ Файл уже в этом формате."
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])