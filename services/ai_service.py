"""
AI сервис для взаимодействия с Gemini/OpenAI API и генерации Python-кода для обработки данных.
"""

import json
import logging
import re
from pathlib import Path
from typing import Optional

import tenacity
from config import config
from utils.profiler_decorator import profiled

logger = logging.getLogger(__name__)


# Системный промпт для AI — загружается из внешнего файла
_SYSTEM_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "system.txt"
SYSTEM_PROMPT = _SYSTEM_PROMPT_PATH.read_text(encoding="utf-8") if _SYSTEM_PROMPT_PATH.exists() else ""

if not SYSTEM_PROMPT:
    SYSTEM_PROMPT = "### [РОЛЬ]\nТы — Senior Data Analyst. Обрабатывай Excel/CSV/Word/TXT.\n### [ФОРМАТ]\nВерни JSON: analysis, code, explanation\n"


# Общие настройки retry для AI API вызовов
_AI_RETRY_CONFIG = {
    "stop": tenacity.stop_after_attempt(3),
    "wait": tenacity.wait_exponential(multiplier=1, min=1, max=8),
    "reraise": False,
    "before_sleep": tenacity.before_sleep_log(logger, logging.WARNING),
}


def _is_retryable_openai(exc: BaseException) -> bool:
    """Только сетевые ошибки/5xx/429 — retry-able."""
    import openai
    return isinstance(exc, (
        openai.RateLimitError,
        openai.APITimeoutError,
        openai.APIConnectionError,
        openai.InternalServerError,
    ))


def _is_retryable_gemini(exc: BaseException) -> bool:
    """Только сетевые ошибки/5xx/429 — retry-able."""
    import google.api_core.exceptions as google_exc
    return isinstance(exc, (
        google_exc.ResourceExhausted,
        google_exc.ServiceUnavailable,
        google_exc.DeadlineExceeded,
    ))


class AIService:
    """Сервис для взаимодействия с AI провайдером (Gemini или OpenAI)."""

    def __init__(self):
        self.provider = config.AI_PROVIDER
        self._init_provider()

    def _init_provider(self):
        """Инициализация выбранного провайдера."""
        if self.provider == "gemini":
            from google import genai
            self._gemini_client = genai.Client(api_key=config.GEMINI_API_KEY)
            self._gemini_model = config.GEMINI_MODEL
            logger.info("Используется Gemini: %s", self._gemini_model)
        else:
            from openai import OpenAI
            self._openai_client = OpenAI(api_key=config.OPENAI_API_KEY)
            self._openai_model = config.OPENAI_MODEL
            self._openai_max_tokens = config.OPENAI_MAX_TOKENS
            self._openai_temperature = config.OPENAI_TEMPERATURE
            logger.info("Используется OpenAI: %s", self._openai_model)

    def _build_prompt(self, user_command: str, file_summaries: list[str],
                      input_path: str, output_path: str) -> str:
        """Сформировать полный промпт для генерации кода."""
        system_prompt = SYSTEM_PROMPT.format(
            input_path=input_path,
            output_path=output_path,
        )
        files_context = "\n\n".join(file_summaries) if file_summaries else "Файлы не загружены."

        return f"""{system_prompt}

## Команда пользователя:
{user_command}

## Информация о файлах:
{files_context}

## Путь к входному файлу (чтение):
{input_path}

## Путь для сохранения результата:
{output_path}

Сгенерируй Python-код для выполнения задачи."""

    # ═══════════════════════════════════════════════════════
    # Retry-обёртка: tenacity для сетевых ошибок/5xx/429
    # ═══════════════════════════════════════════════════════

    @profiled()
    @tenacity.retry(
        stop=_AI_RETRY_CONFIG["stop"],
        wait=_AI_RETRY_CONFIG["wait"],
        retry=tenacity.retry_if_exception(_is_retryable_gemini),
        before_sleep=_AI_RETRY_CONFIG["before_sleep"],
    )
    def _call_gemini_retried(self, prompt: str, json_mode: bool = True) -> str:
        """Gemini-вызов с tenacity (только retry-able ошибки)."""
        config_dict = {}
        if json_mode:
            config_dict["response_mime_type"] = "application/json"
        response = self._gemini_client.models.generate_content(
            model=self._gemini_model,
            contents=prompt,
            config=config_dict,
        )
        text = response.text
        if text is None:
            raise ValueError("Gemini returned empty response")
        return text

    def _call_gemini(self, prompt: str, json_mode: bool = True) -> Optional[str]:
        """Вызов Gemini API + retry + fallback для не-retryable ошибок."""
        try:
            return self._call_gemini_retried(prompt, json_mode=json_mode)
        except Exception as e:
            logger.error("Gemini error: %s: %s", type(e).__name__, e)
            return None

    @profiled()
    @tenacity.retry(
        stop=_AI_RETRY_CONFIG["stop"],
        wait=_AI_RETRY_CONFIG["wait"],
        retry=tenacity.retry_if_exception(_is_retryable_openai),
        before_sleep=_AI_RETRY_CONFIG["before_sleep"],
    )
    def _call_openai_retried(self, prompt: str, json_mode: bool = True) -> str:
        """OpenAI-вызов с tenacity (только retry-able ошибки)."""
        kwargs = {
            "model": self._openai_model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": self._openai_max_tokens,
            "temperature": self._openai_temperature,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        response = self._openai_client.chat.completions.create(**kwargs)
        content = response.choices[0].message.content
        if content is None:
            raise ValueError("OpenAI returned empty response")
        return content

    def _call_openai(self, prompt: str, json_mode: bool = True) -> Optional[str]:
        """Вызов OpenAI API + retry + fallback для не-retryable ошибок."""
        try:
            return self._call_openai_retried(prompt, json_mode=json_mode)
        except Exception as e:
            logger.error("OpenAI error: %s: %s", type(e).__name__, e)
            return None

    @profiled()
    def generate_code(
        self,
        user_command: str,
        file_summaries: list[str],
        input_path: str,
        output_path: str,
    ) -> Optional[dict]:
        """
        Отправить запрос AI и получить сгенерированный код.

        Returns:
            dict с ключами: analysis, code, explanation или None при ошибке.
        """
        try:
            prompt = self._build_prompt(user_command, file_summaries, input_path, output_path)

            if self.provider == "gemini":
                raw = self._call_gemini(prompt, json_mode=True)
            else:
                raw = self._call_openai(prompt, json_mode=True)

            if not raw:
                logger.warning("Пустой ответ от AI")
                return None

            parsed = self._parse_response(raw.strip())
            if parsed is None:
                logger.warning("Не удалось распарсить ответ AI")
            return parsed

        except Exception as e:
            logger.error("AI generate_code error: %s: %s", type(e).__name__, e)
            return None

    def chat_direct(
        self,
        user_message: str,
        file_summaries: list[str] | None = None,
        history: list[dict] | None = None,
    ) -> str:
        """Прямой чат с AI (без генерации кода)."""
        system = (
            "Ты — Senior Data Analyst и эксперт по автоматизации Python. "
            "Отвечай на русском языке, деловым и технически грамотным тоном. "
            "Будь лаконичным и полезным."
        )

        if file_summaries:
            context = "\n\n".join(file_summaries)
            full_prompt = f"{system}\n\nИнформация о файлах:\n{context}\n\n{user_message}"
        else:
            full_prompt = f"{system}\n\n{user_message}"

        try:
            if self.provider == "gemini":
                response = self._gemini_client.models.generate_content(
                    model=self._gemini_model,
                    contents=full_prompt,
                )
                return response.text.strip()
            else:
                response = self._openai_client.chat.completions.create(
                    model=self._openai_model,
                    messages=[{"role": "user", "content": full_prompt}],
                    max_tokens=self._openai_max_tokens,
                    temperature=0.5,
                )
                return response.choices[0].message.content.strip()
        except Exception as e:
            return f"❌ Ошибка при обращении к AI: {e}"

    def _parse_response(self, content: str) -> Optional[dict]:
        """Извлечь JSON из ответа AI."""
        content = content.strip()

        # Пытаемся найти JSON в markdown-блоке
        json_match = re.search(r"```(?:json)?\s*\n(.*?)\n```", content, re.DOTALL)
        if json_match:
            content = json_match.group(1).strip()

        # Пробуем распарсить как JSON
        try:
            result = json.loads(content)
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass

        # Пробуем найти { ... } в тексте
        brace_match = re.search(r"\{[^{}]*\}", content, re.DOTALL)
        if brace_match:
            try:
                result = json.loads(brace_match.group(0))
                if isinstance(result, dict):
                    return result
            except json.JSONDecodeError:
                pass

        # Последняя попытка — рекурсивно ищем вложенные скобки
        brace_match = re.search(r"\{.*\}", content, re.DOTALL)
        if brace_match:
            try:
                result = json.loads(brace_match.group(0))
                if isinstance(result, dict):
                    return result
            except json.JSONDecodeError:
                pass

        logger.warning("Не удалось распарсить ответ AI: %.300s", content)
        return None


ai_service = AIService()
