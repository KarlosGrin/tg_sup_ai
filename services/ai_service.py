"""
AI сервис для взаимодействия с Gemini/OpenAI API и генерации Python-кода для обработки данных.
"""

import json
import re
from pathlib import Path
from typing import Optional

from config import config


# Системный промпт для AI — загружается из внешнего файла
_SYSTEM_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "system.txt"
SYSTEM_PROMPT = _SYSTEM_PROMPT_PATH.read_text(encoding="utf-8") if _SYSTEM_PROMPT_PATH.exists() else ""

if not SYSTEM_PROMPT:
    SYSTEM_PROMPT = "### [РОЛЬ]\nТы — Senior Data Analyst. Обрабатывай Excel/CSV/Word/TXT.\n### [ФОРМАТ]\nВерни JSON: analysis, code, explanation\n"


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
            print(f"[AI] Используется Gemini: {self._gemini_model}")
        else:
            from openai import OpenAI
            self._openai_client = OpenAI(api_key=config.OPENAI_API_KEY)
            self._openai_model = config.OPENAI_MODEL
            self._openai_max_tokens = config.OPENAI_MAX_TOKENS
            self._openai_temperature = config.OPENAI_TEMPERATURE
            print(f"[AI] Используется OpenAI: {self._openai_model}")

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

    def _call_gemini(self, prompt: str, json_mode: bool = True) -> Optional[str]:
        """Вызов Gemini API."""
        try:
            config_dict = {}
            if json_mode:
                config_dict["response_mime_type"] = "application/json"

            response = self._gemini_client.models.generate_content(
                model=self._gemini_model,
                contents=prompt,
                config=config_dict,
            )
            return response.text
        except Exception as e:
            print(f"[AI Gemini Error] {e}")
            return None

    def _call_openai(self, prompt: str, json_mode: bool = True) -> Optional[str]:
        """Вызов OpenAI API."""
        try:
            kwargs = {
                "model": self._openai_model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": self._openai_max_tokens,
                "temperature": self._openai_temperature,
            }
            if json_mode:
                kwargs["response_format"] = {"type": "json_object"}

            response = self._openai_client.chat.completions.create(**kwargs)
            return response.choices[0].message.content
        except Exception as e:
            print(f"[AI OpenAI Error] {e}")
            return None

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
                print("[AI Service Error] Пустой ответ от AI")
                return None

            parsed = self._parse_response(raw.strip())
            if parsed is None:
                print(f"[AI Service Error] Не удалось распарсить ответ AI")
            return parsed

        except Exception as e:
            print(f"[AI Service Error] {type(e).__name__}: {e}")
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

        print(f"[AI Parse Error] Не удалось распарсить ответ: {content[:300]}")
        return None


ai_service = AIService()
