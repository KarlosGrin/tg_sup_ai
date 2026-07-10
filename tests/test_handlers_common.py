"""
Unit-тесты для хелперов из handlers/common.py.
"""

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from handlers.common import (
    _check_rate_limit, _detect_target_extension,
    _cleanup_stale_entries, _track_user, _known_users,
    _user_request_times, _user_upload_times, user_sessions,
    _get_session, _is_admin,
)
from config import config


@pytest.fixture(autouse=True)
def _clean_globals():
    """Очищаем глобальное состояние перед каждым тестом."""
    _user_request_times.clear()
    _user_upload_times.clear()
    user_sessions.clear()
    _known_users.clear()
    yield


# ===== _check_rate_limit =====

class TestCheckRateLimit:
    """Тесты rate limiting."""

    def test_allows_up_to_max_requests(self):
        """Разрешает N запросов в окне."""
        for i in range(5):
            assert _check_rate_limit(1, _user_request_times, 5, 60)

    def test_blocks_n_plus_first(self):
        """Блокирует N+1-й запрос."""
        for i in range(5):
            _check_rate_limit(1, _user_request_times, 5, 60)
        assert not _check_rate_limit(1, _user_request_times, 5, 60)

    def test_allows_after_window_expires(self, mocker):
        """После истечения окна снова разрешает."""
        mock_time = mocker.patch("handlers.common.time")
        # Первые 5 запросов
        mock_time.time.return_value = 1000.0
        for i in range(5):
            _check_rate_limit(1, _user_request_times, 5, 60)

        # 6-й запрос через 61 секунду — окно истекло
        mock_time.time.return_value = 1061.0
        assert _check_rate_limit(1, _user_request_times, 5, 60)

    def test_different_users_independent(self):
        """Разные пользователи не влияют друг на друга."""
        for i in range(3):
            _check_rate_limit(1, _user_request_times, 3, 60)
        for i in range(3):
            _check_rate_limit(2, _user_request_times, 3, 60)
        # user 1 — лимит
        assert not _check_rate_limit(1, _user_request_times, 3, 60)
        # user 2 — лимит
        assert not _check_rate_limit(2, _user_request_times, 3, 60)
        # user 3 — ещё есть место
        assert _check_rate_limit(3, _user_request_times, 3, 60)

    def test_clears_old_entries(self, mocker):
        """Старые записи (вне окна) удаляются."""
        mock_time = mocker.patch("handlers.common.time")
        mock_time.time.return_value = 1000.0
        _check_rate_limit(1, _user_request_times, 5, 60)  # запрос в t=1000
        mock_time.time.return_value = 1100.0
        # В t=1100 запрос в t=1000 уже вне окна (старше 60с)
        result = _check_rate_limit(1, _user_request_times, 5, 60)
        assert result  # должен быть разрешён (старая запись удалена)


# ===== _detect_target_extension =====

class TestDetectTargetExtension:
    """Тесты определения целевого расширения."""

    def test_xlsx_by_name(self):
        """Упоминание 'xlsx' → .xlsx."""
        assert _detect_target_extension("сохрани как xlsx", ".csv") == ".xlsx"

    def test_csv_by_name(self):
        """Упоминание 'csv' → .csv."""
        assert _detect_target_extension("экспорт в csv", ".xlsx") == ".csv"

    def test_txt_by_name(self):
        """Упоминание 'txt' → .txt."""
        assert _detect_target_extension("сохрани как txt", ".xlsx") == ".txt"

    def test_docx_by_word(self):
        """Упоминание 'word' → .docx."""
        assert _detect_target_extension("сохрани в word", ".csv") == ".docx"

    def test_excel_russian(self):
        """Упоминание 'эксель' → .xlsx."""
        assert _detect_target_extension("сделай эксель", ".csv") == ".xlsx"

    def test_no_keyword_returns_source(self):
        """Без ключевого слова → возвращается source_ext."""
        assert _detect_target_extension("просто сохрани", ".xlsx") == ".xlsx"

    def test_empty_command_returns_source(self):
        """Пустая команда → source_ext."""
        assert _detect_target_extension("", ".csv") == ".csv"

    def test_case_insensitive(self):
        """Команда в любом регистре."""
        assert _detect_target_extension("CSV FORMAT", ".xlsx") == ".csv"
        assert _detect_target_extension("Excel", ".csv") == ".xlsx"


# ===== _cleanup_stale_entries =====

class TestCleanupStaleEntries:
    """Тесты очистки мёртвых записей."""

    def test_removes_old_rate_limit_entries(self, mocker):
        """Записи rate-limit без активности > 1ч удаляются."""
        mock_time = mocker.patch("handlers.common.time")
        # Добавляем запись в t=1000
        mock_time.time.return_value = 1000.0
        _check_rate_limit(1, _user_request_times, 5, 60)
        assert 1 in _user_request_times

        # Чистим в t=5000 (> 1ч = 3600с разницы)
        mock_time.time.return_value = 5000.0
        _cleanup_stale_entries()
        assert 1 not in _user_request_times

    def test_keeps_fresh_rate_limit_entries(self, mocker):
        """Свежие записи rate-limit не удаляются."""
        mock_time = mocker.patch("handlers.common.time")
        mock_time.time.return_value = 1000.0
        _check_rate_limit(1, _user_request_times, 5, 60)

        # Чистим через 30 минут (меньше 1ч)
        mock_time.time.return_value = 1000.0 + 1800
        _cleanup_stale_entries()
        assert 1 in _user_request_times

    def test_removes_empty_sessions_not_known(self, mocker):
        """Пустые сессии без _known_users удаляются."""
        mock_time = mocker.patch("handlers.common.time")
        mock_time.time.return_value = 1000.0

        # Создаём сессию напрямую (без _track_user, чтобы не попасть в _known_users)
        user_sessions[999] = {"file_paths": [], "last_code": "", "history": []}
        assert 999 in user_sessions

        # Чистим
        _cleanup_stale_entries()
        assert 999 not in user_sessions

    def test_keeps_known_users_empty_sessions(self, mocker):
        """Сессии известных пользователей (_known_users) не удаляются даже пустые."""
        mock_time = mocker.patch("handlers.common.time")
        mock_time.time.return_value = 1000.0

        _track_user(42)
        session = _get_session(42)
        assert 42 in user_sessions

        # Чистим — 42 в _known_users, сессия не удаляется
        _cleanup_stale_entries()
        assert 42 in user_sessions

    def test_keeps_sessions_with_files(self, mocker):
        """Сессии с файлами не удаляются."""
        mock_time = mocker.patch("handlers.common.time")
        mock_time.time.return_value = 1000.0

        session = _get_session(100)
        session["file_paths"] = ["/tmp/test.xlsx"]

        _cleanup_stale_entries()
        assert 100 in user_sessions


# ===== _is_admin =====

class TestIsAdmin:
    """Тесты проверки администратора."""

    def test_admin_id_in_list(self, monkeypatch):
        """ID из списка ADMIN_IDS — админ."""
        monkeypatch.setattr(config, "ADMIN_IDS", [123, 456])
        assert _is_admin(123)
        assert _is_admin(456)

    def test_non_admin_id_not_in_list(self, monkeypatch):
        """ID не из списка — не админ."""
        monkeypatch.setattr(config, "ADMIN_IDS", [123, 456])
        assert not _is_admin(789)

    def test_empty_admin_list(self, monkeypatch):
        """Пустой список ADMIN_IDS — никто не админ."""
        monkeypatch.setattr(config, "ADMIN_IDS", [])
        assert not _is_admin(123)
        assert not _is_admin(0)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])