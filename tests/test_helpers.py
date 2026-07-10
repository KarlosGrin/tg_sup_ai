"""
Unit-тесты для utils/helpers.py.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.helpers import (
    clean_filename,
    format_size,
    sanitize_for_markdown,
    sanitize_log,
)


class TestCleanFilename:
    """Тесты clean_filename."""

    def test_removes_invalid_chars(self):
        """Недопустимые символы заменяются на '_'."""
        result = clean_filename('file:name<>test.txt')
        assert result == 'file_name__test.txt'

    def test_keeps_valid_chars(self):
        """Допустимые символы не изменяются."""
        result = clean_filename('test_file_123.xlsx')
        assert result == 'test_file_123.xlsx'


class TestFormatSize:
    """Тесты format_size."""

    def test_bytes(self):
        assert format_size(500) == "500.0 Б"

    def test_kilobytes(self):
        assert format_size(2048) == "2.0 КБ"

    def test_megabytes(self):
        assert format_size(5 * 1024 * 1024) == "5.0 МБ"

    def test_gigabytes(self):
        assert format_size(3 * 1024 * 1024 * 1024) == "3.0 ГБ"


class TestSanitizeForMarkdown:
    """Тесты sanitize_for_markdown."""

    def test_closes_unclosed_bold(self):
        """Незакрытые ** закрываются."""
        result = sanitize_for_markdown("**bold text")
        assert result.endswith("**")

    def test_closes_unclosed_italic(self):
        """Незакрытые _ закрываются."""
        result = sanitize_for_markdown("_italic text")
        assert result.endswith("_")

    def test_closes_unclosed_code(self):
        """Незакрытые ` закрываются."""
        result = sanitize_for_markdown("`code text")
        assert result.endswith("`")

    def test_leaves_closed_markdown(self):
        """Корректный Markdown не изменяется."""
        text = "**bold** and _italic_ and `code`"
        result = sanitize_for_markdown(text)
        assert result == text

    def test_multiline_mixed(self):
        """Многострочный текст с разными проблемами."""
        text = "**bold\n_italic\n`code"
        result = sanitize_for_markdown(text)
        lines = result.split("\n")
        assert lines[0].endswith("**")
        assert lines[1].endswith("_")
        assert lines[2].endswith("`")


class TestSanitizeLog:
    """Тесты sanitize_log."""

    def test_hides_openai_key(self):
        """OpenAI API ключ маскируется."""
        text = "Using key sk-proj-ABCDEF1234567890abcdef1234567890abcdef12"
        result = sanitize_log(text)
        assert "***REDACTED***" in result
        assert "ABCDEF1234567890" not in result

    def test_hides_gemini_key(self):
        """Gemini API ключ маскируется."""
        text = "Gemini key: AIzaSyA1b2C3d4E5f6G7h8I9j0KlMnOpQrStUvWxYz"
        result = sanitize_log(text)
        assert "***REDACTED***" in result

    def test_leaves_safe_text(self):
        """Безопасный текст не изменяется."""
        text = "Hello, this is a normal log message"
        result = sanitize_log(text)
        assert result == text

    def test_empty_string(self):
        """Пустая строка не вызывает ошибок."""
        assert sanitize_log("") == ""

    def test_multiple_keys(self):
        """Несколько ключей в одном тексте."""
        text = "Key1=sk-proj-AAAAABBBBBCCCCCDDDDD, key2=AIzaSyA1b2C3d4E5f6G7h8I9j0KlMnOpQrStUvWxYz"
        result = sanitize_log(text)
        assert "***REDACTED***" in result
        assert "AAAAABBBBBCCCCCDDDDD" not in result


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])