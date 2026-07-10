"""
Gemini Assistant — гибридный режим: загрузка в File API + локальный exec.

ВНИМАНИЕ: Этот режим НЕ выполняет код в песочнице Gemini.
Gemini читает файл через File API и генерирует Python-код,
НО код выполняется ЛОКАЛЬНО через code_executor (с sandbox-ограничениями).

Использование:
    EXECUTION_MODE=assistants в .env + AI_PROVIDER=gemini
"""

import asyncio
import logging
import os
import json
import tempfile
import re
from pathlib import Path
from typing import Optional

import tenacity
from config import config
from utils.profiler_decorator import profiled

logger = logging.getLogger(__name__)


def _is_retryable_gemini(exc: BaseException) -> bool:
    """Определяет, можно ли повторить Gemini запрос при этой ошибке."""
    import google.api_core.exceptions as google_exc
    return isinstance(exc, (
        google_exc.ResourceExhausted,
        google_exc.ServiceUnavailable,
        google_exc.DeadlineExceeded,
    ))


# Системный промпт для AI — загружается из внешнего файла
_SYSTEM_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "system.txt"
SYSTEM_PROMPT = _SYSTEM_PROMPT_PATH.read_text(encoding="utf-8") if _SYSTEM_PROMPT_PATH.exists() else ""


class GeminiAssistant:
    """
    Гибридный режим: Gemini читает файл через File API, генерирует код,
    код выполняется локально (НЕ в песочнице Gemini).
    """

    def __init__(self):
        from google import genai
        self.client = genai.Client(api_key=config.GEMINI_API_KEY)
        self.model = config.GEMINI_MODEL
        self._tmp_files: list[str] = []
        logger.info("Гибридный режим, модель: %s", self.model)

    @profiled()
    async def execute(self, code: str, input_path: str, output_path: str, user_command: str = "") -> dict:
        """
        Загрузить файл в Gemini File API → Gemini генерирует код →
        выполнить код локально.

        Args:
            code: Python-код (не используется в этом режиме, параметр для совместимости).
            input_path: Путь к исходному файлу.
            output_path: Путь для сохранения результата.
            user_command: Команда пользователя (текст запроса).

        Returns:
            dict: {success, stdout, stderr, output_path}
        """
        result = {
            "success": False,
            "stdout": "",
            "stderr": "",
            "output_path": output_path,
        }

        try:
            # 1. Создаём ASCII-safe копию для Gemini
            ascii_path = self._make_ascii_copy(input_path)
            if not ascii_path:
                result["stderr"] = "❌ Не удалось подготовить файл для загрузки."
                return result

            # 2. Загружаем файл в Gemini File API
            file_uri = await self._upload_file(ascii_path)
            if not file_uri:
                result["stderr"] = "❌ Не удалось загрузить файл в Gemini."
                return result

            # 3. Gemini генерирует код (читает файл нативно через File API)
            file_name = Path(input_path).name
            ai_result = await self._generate_code(file_name, file_uri, input_path, output_path, user_command)

            if not ai_result:
                result["stderr"] = "❌ Gemini не смог сгенерировать код."
                return result

            analysis_text = ai_result.get("analysis", "")
            code_text = ai_result.get("code", "")
            explanation_text = ai_result.get("explanation", "")

            result["stdout"] = f"📋 Анализ: {analysis_text}\n💡 Пояснение: {explanation_text}"
            result["stderr"] = ""

            # 4. Выполняем код ЛОКАЛЬНО (не в песочнице Gemini)
            if code_text:
                from services.code_executor import code_executor
                exec_result = code_executor.execute(
                    code=code_text,
                    input_path=input_path,
                    output_path=output_path,
                )
                result["success"] = exec_result["success"]
                if exec_result["stdout"]:
                    result["stdout"] += f"\n\n{exec_result['stdout']}"
                if exec_result["stderr"]:
                    result["stderr"] += f"\n{exec_result['stderr']}"
            else:
                result["success"] = True

            return result

        except Exception as e:
            result["stderr"] = f"❌ Gemini Assistant error: {type(e).__name__}: {e}"
            return result
        finally:
            self._cleanup_tmp()

    async def _generate_code(
        self, file_name: str, file_uri: str, input_path: str, output_path: str,
        user_command: str = "",
    ) -> Optional[dict]:
        """Отправить файл (через URI) в Gemini и получить сгенерированный код."""
        prompt = self._build_prompt(file_name, file_uri, input_path, output_path, user_command)

        try:
            # Retry-обёртка для Gemini API вызова
            @tenacity.retry(
                stop=tenacity.stop_after_attempt(3),
                wait=tenacity.wait_exponential(multiplier=1, min=1, max=8),
                retry=tenacity.retry_if_exception(
                    lambda e: _is_retryable_gemini(e)
                ),
                before_sleep=tenacity.before_sleep_log(logger, logging.WARNING),
                reraise=True,
            )
            def _call_gemini():
                return self.client.models.generate_content(
                    model=self.model,
                    contents=prompt,
                    config={"response_mime_type": "application/json"},
                )

            response = await asyncio.to_thread(_call_gemini)

            if not response or not response.text:
                logger.warning("Пустой ответ от Gemini")
                return None

            raw = response.text.strip()
            parsed = self._parse_response(raw)
            if parsed is None:
                logger.warning("Не удалось распарсить JSON ответ Gemini")
                # Пробуем извлечь код из markdown
                code_match = re.search(r"```python\n(.*?)\n```", raw, re.DOTALL)
                if code_match:
                    return {
                        "analysis": "Сгенерирован код обработки",
                        "code": code_match.group(1),
                        "explanation": raw[:500],
                    }
                return None
            return parsed

        except Exception as e:
            logger.error("Gemini call error: %s: %s", type(e).__name__, e)
            return None

    # ----------------------------------------------------------------
    # Upload / File API
    # ----------------------------------------------------------------

    async def _upload_file(self, file_path: str) -> Optional[str]:
        """Загрузить файл в Gemini File API (async), вернуть URI."""
        try:
            file_path_resolved = str(Path(file_path).resolve())
            logger.info("Загрузка файла: %s", file_path_resolved)

            if not Path(file_path_resolved).exists():
                logger.warning("Файл не найден: %s", file_path_resolved)
                return None

            file = await asyncio.to_thread(
                lambda fp: self.client.files.upload(file=fp), file_path_resolved
            )

            while file.state.name == "PROCESSING":
                await asyncio.sleep(0.5)
                file = await asyncio.to_thread(
                    lambda fid: self.client.files.get(name=fid), file.name
                )

            if file.state.name == "FAILED":
                logger.warning("Ошибка загрузки: %s", file.state.name)
                return None

            logger.info("Файл загружен: %s", file.uri)
            return file.uri

        except Exception as e:
            logger.error("Ошибка upload: %s: %s", type(e).__name__, e)
            return None

    # ----------------------------------------------------------------
    # Промпт и парсинг
    # ----------------------------------------------------------------

    def _build_prompt(self, file_name: str, file_uri: str, input_path: str, output_path: str,
                      user_command: str = "") -> str:
        """Сформировать промпт: Gemini читает файл через File API и генерирует код."""
        ext = Path(input_path).suffix.lower()
        system = SYSTEM_PROMPT.format(input_path=input_path, output_path=output_path) if SYSTEM_PROMPT else ""
        user_cmd = user_command if user_command else "Выполни анализ данных и сохрани результат в файл."

        return f"""{system}

## Файл для обработки
Файл '{file_name}' загружен в Gemini File API.
Ты используешь URI {file_uri} ТОЛЬКО для чтения содержимого своими глазами (через File API Gemini).
НЕ включай этот URI в генерируемый Python-код.

## Команда пользователя
{user_cmd}

## 🔴 ВЕРСИЯ БИБЛИОТЕК
- **pandas 3.0+** — `fillna(method='ffill')` удалён, используй `df.ffill()` или `df.fillna(method='ffill')`→ заменить на `df.ffill()`
- **openpyxl 3.1+**
- **numpy 2.5+**

## 🔴 КРИТИЧЕСКИ ВАЖНО: ПЕРЕМЕННЫЕ УЖЕ ДОСТУПНЫ
В коде уже объявлены и доступны следующие переменные:
- `input_path` — полный путь к входному файлу (НЕ создавай свою переменную)
- `output_path` — полный путь для сохранения результата (НЕ создавай свою переменную)
- `output_dir` — директория для сохранения (НЕ создавай свою переменную)
- `pd` / `pandas` — уже импортирован
- `np` / `numpy` — уже импортирован
- `pathlib.Path` — уже импортирован (используй для работы с путями)

НЕ ПИШИ: `input_path = "..."` или `output_path = "..."` — используй готовые переменные.
НЕ ПИШИ: `import pandas as pd` — pd уже есть.
НЕ ПИШИ: `import os` — модуль os НЕ ДОСТУПЕН.
НЕ ИСПОЛЬЗУЙ: `requests`, `urllib`, `httpx`, `aiohttp` — эти модули ЗАБЛОКИРОВАНЫ в среде исполнения.
НЕ ИСПОЛЬЗУЙ: URI вида `https://` или `files/` для чтения файла в коде — код выполняется локально, без сети.

Для чтения файла в коде используй ТОЛЬКО переменную `input_path`:
ПРАВИЛЬНО: `df = pd.read_excel(input_path)`
ПРАВИЛЬНО: `with open(input_path, "r") as f: content = f.read()`
ПРАВИЛЬНО: `doc = Document(input_path)`
НЕПРАВИЛЬНО: `requests.get(file_uri)` — requests заблокирован!
НЕПРАВИЛЬНО: `input_path = 'downloads/file.xlsx'` (перезапишет переменную!)

## Важно
- Прочитай файл через File API Gemini (URI выше) ТОЛЬКО для понимания структуры.
- В генерируемом Python-коде читай файл через `input_path`.
- Сгенерируй Python-код для обработки данных.
- Верни ТОЛЬКО JSON: analysis, code, explanation.

## Формат ответа
{{"analysis": "что будет сделано", "code": "Python код", "explanation": "пояснение"}}"""

    @staticmethod
    def _parse_response(content: str) -> Optional[dict]:
        """Извлечь JSON из ответа AI."""
        content = content.strip()

        json_match = re.search(r"```(?:json)?\s*\n(.*?)\n```", content, re.DOTALL)
        if json_match:
            content = json_match.group(1).strip()

        try:
            result = json.loads(content)
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass

        brace_match = re.search(r"\{[^{}]*\}", content, re.DOTALL)
        if brace_match:
            try:
                result = json.loads(brace_match.group(0))
                if isinstance(result, dict):
                    return result
            except json.JSONDecodeError:
                pass

        logger.warning("Не удалось распарсить JSON ответ Gemini")
        return None

    # ----------------------------------------------------------------
    # Вспомогательные
    # ----------------------------------------------------------------

    def _make_ascii_copy(self, file_path: str) -> Optional[str]:
        """Создать ASCII-safe копию файла."""
        try:
            src = Path(file_path)
            if not src.exists():
                return None
            suffix = src.suffix.lower() or ".bin"
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
            tmp_path = tmp.name
            tmp.close()
            import shutil
            shutil.copy2(str(src.resolve()), tmp_path)
            self._tmp_files.append(tmp_path)
            return tmp_path
        except Exception as e:
            logger.warning("Ошибка копирования: %s", e)
            return None

    def _cleanup_tmp(self):
        """Удалить временные ASCII-копии."""
        for p in self._tmp_files:
            try:
                os.unlink(p)
            except OSError:
                pass
        self._tmp_files.clear()


# Глобальный экземпляр
gemini_assistant = GeminiAssistant()
