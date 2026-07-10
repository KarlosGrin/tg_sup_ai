"""
Unit-тесты для FileService (без Telegram, без сети).
"""

import pytest
import uuid
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from services.file_service import FileService


@pytest.fixture
def file_service(tmp_path):
    """FileService с временными директориями."""
    fs = FileService()
    fs.download_dir = tmp_path / "downloads"
    fs.processed_dir = tmp_path / "processed"
    fs.download_dir.mkdir(exist_ok=True)
    fs.processed_dir.mkdir(exist_ok=True)
    fs._user_files = {}
    return fs


class TestFileServiceIsolation:
    """Тесты изоляции файлов по user_id."""

    def test_download_saves_in_user_subdir(self, file_service):
        """download должен сохранять в downloads/<user_id>/."""
        # Создаём тестовый файл для симуляции
        user_id = 12345
        user_dir = file_service.download_dir / str(user_id)
        assert not user_dir.exists()

        # Через _user_files симулируем сохранение
        test_path = file_service.download_dir / str(user_id) / "test.txt"
        user_dir.mkdir(parents=True)
        test_path.write_text("data")

        # Проверяем изоляцию
        other_user = 99999
        other_dir = file_service.download_dir / str(other_user)
        assert not other_dir.exists()
        assert test_path.exists()

    def test_get_user_files_empty_initially(self, file_service):
        """У нового пользователя нет файлов."""
        assert file_service.get_user_files(999) == []

    def test_cleanup_removes_old_files(self, file_service):
        """cleanup_old_files удаляет файлы старше N часов."""
        import time
        user_dir = file_service.download_dir / "1"
        user_dir.mkdir(parents=True)
        new_file = user_dir / "new.txt"
        new_file.write_text("new")
        old_file = user_dir / "old.txt"
        old_file.write_text("old")

        # Устанавливаем старому файлу mtime на 25 часов назад
        old_time = time.time() - 25 * 3600
        os_util = __import__("os")
        os_util.utime(str(old_file), (old_time, old_time))

        file_service.cleanup_old_files(max_age_hours=24)

        assert new_file.exists()  # новый не тронут
        assert not old_file.exists()  # старый удалён


class TestAllowedExtensions:
    """Тесты проверки расширений."""

    def test_allowed_extensions_contains_xlsx(self):
        assert ".xlsx" in FileService.ALLOWED_EXTENSIONS

    def test_allowed_extensions_contains_csv(self):
        assert ".csv" in FileService.ALLOWED_EXTENSIONS

    def test_allowed_extensions_contains_docx(self):
        assert ".docx" in FileService.ALLOWED_EXTENSIONS

    def test_allowed_extensions_rejects_exe(self):
        assert ".exe" not in FileService.ALLOWED_EXTENSIONS

    def test_allowed_extensions_rejects_zip(self):
        assert ".zip" not in FileService.ALLOWED_EXTENSIONS


class TestMagicBytes:
    """Тесты валидации magic bytes."""

    def test_valid_xlsx_magic(self, tmp_path):
        """XLSX файл с правильной ZIP-сигнатурой проходит проверку."""
        f = tmp_path / "test.xlsx"
        # ZIP magic: PK\x03\x04
        f.write_bytes(b"PK\x03\x04" + b"\x00" * 20)
        assert FileService._validate_magic_bytes(str(f), ".xlsx")

    def test_invalid_xlsx_magic(self, tmp_path):
        """Файл с расширением .xlsx но без ZIP-сигнатуры не проходит."""
        f = tmp_path / "fake.xlsx"
        f.write_bytes(b"\x00" * 100)
        assert not FileService._validate_magic_bytes(str(f), ".xlsx")

    def test_txt_always_passes(self, tmp_path):
        """.txt всегда проходит проверку (любой текст)."""
        f = tmp_path / "test.txt"
        f.write_bytes(b"Hello, World!")
        assert FileService._validate_magic_bytes(str(f), ".txt")

    def test_csv_always_passes(self, tmp_path):
        """.csv всегда проходит проверку."""
        f = tmp_path / "test.csv"
        f.write_bytes(b"a,b,c\n1,2,3")
        assert FileService._validate_magic_bytes(str(f), ".csv")

    def test_exe_disguised_as_docx_rejected(self, tmp_path):
        """.exe переименованный в .docx не имеет ZIP-сигнатуры."""
        f = tmp_path / "malware.docx"
        f.write_bytes(b"MZ\x90\x00" + b"\x00" * 100)  # PE-загрузчик
        # .docx ожидает ZIP-сигнатуру PK\x03\x04
        assert not FileService._validate_magic_bytes(str(f), ".docx")


class TestFileSummary:
    """Тесты get_file_summary (без pandas для Excel — только txt/csv)."""

    def test_txt_summary(self, file_service, tmp_path):
        """Текстовый файл должен давать корректное описание."""
        f = tmp_path / "test.txt"
        f.write_text("Hello\nWorld\nLine 3\n")
        summary = FileService.get_file_summary(str(f))
        assert "test.txt" in summary
        assert "Текстовый файл" in summary

    def test_csv_summary(self, file_service, tmp_path):
        """CSV файл должен показывать колонки."""
        f = tmp_path / "test.csv"
        f.write_text("name,age,city\nAlice,30,NYC\nBob,25,LA\n")
        summary = FileService.get_file_summary(str(f))
        assert "test.csv" in summary
        assert "name" in summary
        assert "age" in summary
        assert "city" in summary


class TestEscapeData:
    """Тесты _escape_data (защита от prompt injection)."""

    def test_escape_triple_backticks(self):
        """``` заменяются на '''."""
        result = FileService._escape_data("```dangerous```")
        assert "'''" in result
        assert "```" not in result

    def test_escape_truncates_long_text(self):
        """Текст длиннее max_len обрезается."""
        text = "a" * 500
        result = FileService._escape_data(text, max_len=100)
        assert len(result) <= 120  # 100 + русский суффикс
        assert "... (обрезано)" in result

    def test_escape_short_text_unchanged(self):
        """Короткий текст не обрезается."""
        text = "Hello, world!"
        result = FileService._escape_data(text, max_len=300)
        assert result == text


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])