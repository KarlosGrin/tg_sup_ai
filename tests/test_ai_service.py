"""
Unit-тесты для AIService (без реальных сетевых вызовов).
"""

import json
import sys
from pathlib import Path
from typing import Optional

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from services.ai_service import AIService, _is_retryable_openai, _is_retryable_gemini
from services.ai_service import _AI_RETRY_CONFIG


# ===== _parse_response =====

class TestParseResponse:
    """Тесты _parse_response: извлечение JSON из ответа AI."""

    @pytest.fixture
    def service(self):
        s = AIService()
        s._openai_client = None  # не нужен для _parse_response
        return s

    def test_valid_json(self, service):
        """Чистый JSON парсится корректно."""
        result = service._parse_response('{"analysis": "test", "code": "print(1)"}')
        assert result is not None
        assert result["analysis"] == "test"
        assert result["code"] == "print(1)"

    def test_json_in_markdown_block(self, service):
        """JSON внутри ```json ... ``` блока."""
        text = '```json\n{"analysis": "test", "code": "print(1)"}\n```'
        result = service._parse_response(text)
        assert result is not None
        assert result["analysis"] == "test"

    def test_json_with_surrounding_text(self, service):
        """JSON с мусором вокруг."""
        text = 'Вот результат:\n{"analysis": "a", "code": "c"}\nКонец.'
        result = service._parse_response(text)
        assert result is not None
        assert result["analysis"] == "a"

    def test_json_in_braces(self, service):
        """JSON в фигурных скобках внутри текста."""
        text = 'Some text {"analysis": "a", "code": "c"} more text'
        result = service._parse_response(text)
        assert result is not None
        assert result["analysis"] == "a"

    def test_invalid_response_returns_none(self, service):
        """Невалидный ответ → None."""
        result = service._parse_response("Это не JSON и не содержит JSON")
        assert result is None

    def test_empty_string_returns_none(self, service):
        """Пустая строка → None."""
        result = service._parse_response("")
        assert result is None

    def test_nested_json(self, service):
        """Вложенный JSON парсится."""
        text = '{"analysis": "a", "code": "print(1)", "meta": {"version": 2}}'
        result = service._parse_response(text)
        assert result is not None
        assert result["meta"]["version"] == 2

    def test_json_with_escaped_chars(self, service):
        """JSON с экранированными символами."""
        text = '{"analysis": "line1\\nline2", "code": ""}'
        result = service._parse_response(text)
        assert result is not None
        assert "line1" in result["analysis"]


# ===== Retry predicates =====

def _make_openai_exc(exc_cls, message="test error"):
    """Создать исключение openai с корректными аргументами конструктора."""
    import httpx
    import openai
    import inspect

    sig = inspect.signature(exc_cls.__init__)
    params = list(sig.parameters.keys())

    if "response" in params:
        mock_resp = httpx.Response(500, request=httpx.Request("POST", "https://api.openai.com"))
        return exc_cls(message, response=mock_resp, body=None)
    elif "request" in params:
        mock_req = httpx.Request("POST", "https://api.openai.com")
        if "message" in params:
            return exc_cls(message=message, request=mock_req)
        return exc_cls(mock_req)
    return exc_cls(message)


class TestRetryPredicates:
    """Тесты _is_retryable_openai / _is_retryable_gemini."""

    def test_openai_rate_limit_is_retryable(self):
        """RateLimitError — retry-able."""
        import openai
        exc = _make_openai_exc(openai.RateLimitError)
        assert _is_retryable_openai(exc)

    def test_openai_timeout_is_retryable(self):
        """APITimeoutError — retry-able."""
        import openai
        exc = _make_openai_exc(openai.APITimeoutError)
        assert _is_retryable_openai(exc)

    def test_openai_connection_error_is_retryable(self):
        """APIConnectionError — retry-able."""
        import openai
        exc = _make_openai_exc(openai.APIConnectionError)
        assert _is_retryable_openai(exc)

    def test_openai_internal_server_error_is_retryable(self):
        """InternalServerError (5xx) — retry-able."""
        import openai
        exc = _make_openai_exc(openai.InternalServerError)
        assert _is_retryable_openai(exc)

    def test_openai_bad_request_not_retryable(self):
        """BadRequestError (4xx) — НЕ retry-able."""
        import openai
        exc = _make_openai_exc(openai.BadRequestError)
        assert not _is_retryable_openai(exc)

    def test_openai_auth_error_not_retryable(self):
        """AuthenticationError (401) — НЕ retry-able."""
        import openai
        exc = _make_openai_exc(openai.AuthenticationError)
        assert not _is_retryable_openai(exc)

    def test_retry_config_has_3_attempts(self):
        """Конфиг retry: 3 попытки."""
        assert _AI_RETRY_CONFIG["stop"].max_attempt_number == 3

    def test_retry_config_exponential_wait(self):
        """Конфиг retry: экспоненциальная пауза 1-8с."""
        import tenacity
        wait = _AI_RETRY_CONFIG["wait"]
        assert isinstance(wait, tenacity.wait_exponential)
        # tenacity 9.x: min_wait/max_wait или min/max
        min_w = getattr(wait, "min_wait", getattr(wait, "min", None))
        max_w = getattr(wait, "max_wait", getattr(wait, "max", None))
        assert min_w == 1
        assert max_w == 8


# ===== Retry with mocks =====

class TestRetryWithMock:
    """Тесты retry-логики через мок клиента."""

    @pytest.fixture
    def service(self, mocker):
        """AIService с замоканным OpenAI клиентом."""
        import httpx
        import openai
        s = AIService()
        s._openai_model = "gpt-4o"
        s._openai_max_tokens = 100
        s._openai_temperature = 0.3
        s._openai_client = mocker.MagicMock()
        return s

    def _make_openai_exc(self, exc_cls):
        return _make_openai_exc(exc_cls)

    def test_retry_success_on_second_attempt(self, service, mocker):
        """Первый вызов падает, второй успешен — итог success."""
        import openai
        mock_response = mocker.MagicMock()
        mock_response.choices[0].message.content = '{"analysis": "ok", "code": ""}'
        service._openai_client.chat.completions.create.side_effect = [
            _make_openai_exc(openai.RateLimitError),
            mock_response,
        ]

        result = service._call_openai_retried("test prompt")
        assert result == '{"analysis": "ok", "code": ""}'
        assert service._openai_client.chat.completions.create.call_count == 2

    def test_retry_exhaustion_returns_none(self, service):
        """Все 3 попытки падают — возвращается None (через _call_openai)."""
        import openai
        exc = _make_openai_exc(openai.APITimeoutError)
        service._openai_client.chat.completions.create.side_effect = (exc for _ in range(5))

        result = service._call_openai("test prompt")
        assert result is None
        assert service._openai_client.chat.completions.create.call_count == 3

    def test_bad_request_no_retry(self, service):
        """BadRequestError (не retry-able) — сразу None, без повторных попыток."""
        import openai
        exc = _make_openai_exc(openai.BadRequestError)
        service._openai_client.chat.completions.create.side_effect = exc

        result = service._call_openai("test prompt")
        assert result is None
        assert service._openai_client.chat.completions.create.call_count == 1


# ===== generate_code integration =====

class TestGenerateCode:
    """Тесты generate_code с моками."""

    def test_generate_code_success(self, mocker):
        """generate_code возвращает распарсенный dict при успешном вызове."""
        import openai
        s = AIService()
        s._openai_model = "gpt-4o"
        s._openai_max_tokens = 100
        s._openai_temperature = 0.3
        s._openai_client = mocker.MagicMock()
        s.provider = "openai"

        mock_response = mocker.MagicMock()
        mock_response.choices[0].message.content = (
            '{"analysis": "test analysis", "code": "print(1)", "explanation": "test"}'
        )
        s._openai_client.chat.completions.create.return_value = mock_response

        result = s.generate_code(
            user_command="test",
            file_summaries=["file1"],
            input_path="/tmp/test.xlsx",
            output_path="/tmp/result.xlsx",
        )
        assert result is not None
        assert result["analysis"] == "test analysis"
        assert result["code"] == "print(1)"

    def test_generate_code_empty_response(self, mocker):
        """Пустой ответ AI → None."""
        s = AIService()
        s._openai_model = "gpt-4o"
        s.provider = "openai"
        s._openai_client = mocker.MagicMock()

        mock_response = mocker.MagicMock()
        mock_response.choices[0].message.content = ""
        s._openai_client.chat.completions.create.return_value = mock_response

        result = s.generate_code(
            user_command="test",
            file_summaries=[],
            input_path="/tmp/test.xlsx",
            output_path="/tmp/result.xlsx",
        )
        assert result is None


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])