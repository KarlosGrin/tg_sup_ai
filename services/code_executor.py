"""
Безопасный исполнитель Python-кода.
Выполняет код в ОТДЕЛЬНОМ ПРОЦЕССЕ (subprocess) с ограниченными builtins
и без доступа к os, subprocess, сети и файловой системе вне разрешённых папок.
"""

import json
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Optional

import pandas as pd


# ══════════════════════════════════════════════════════════════════
# Sandbox-обёртка — запускается в отдельном процессе через subprocess
# ══════════════════════════════════════════════════════════════════
_SANDBOX_BOOTSTRAP = textwrap.dedent("""\
    import sys, json, pathlib, traceback
    from io import StringIO

    # 1. Читаем конфиг из stdin (первая строка — JSON)
    config_line = sys.stdin.readline()
    cfg = json.loads(config_line)

    download_dir = cfg["download_dir"]
    processed_dir = cfg["processed_dir"]
    input_path = cfg["input_path"]
    output_path = cfg["output_path"]
    output_dir = cfg["output_dir"]

    # 2. Ограниченные builtins
    safe_builtins = {
        "True": True, "False": False, "None": None,
        "Ellipsis": Ellipsis, "NotImplemented": NotImplemented,
        "bool": bool, "int": int, "float": float, "complex": complex,
        "str": str, "bytes": bytes, "bytearray": bytearray,
        "list": list, "dict": dict, "tuple": tuple, "set": set, "frozenset": frozenset,
        "object": object, "type": type, "slice": slice,
        "range": range, "enumerate": enumerate, "zip": zip,
        "map": map, "filter": filter, "reversed": reversed, "sorted": sorted,
        "iter": iter, "next": next,
        "len": len, "min": min, "max": max, "sum": sum, "any": any, "all": all,
        "abs": abs, "round": round, "pow": pow, "divmod": divmod,
        "ord": ord, "chr": chr, "repr": repr, "ascii": ascii, "format": format,
        "hash": hash, "id": id,
        "isinstance": isinstance, "issubclass": issubclass,
        "hasattr": hasattr, "getattr": getattr, "setattr": setattr, "delattr": delattr,
        "callable": callable,
        "staticmethod": staticmethod, "classmethod": classmethod,
        "property": property, "super": super,
        "print": print,
        "Exception": Exception, "BaseException": BaseException,
        "ValueError": ValueError, "TypeError": TypeError, "KeyError": KeyError,
        "IndexError": IndexError, "AttributeError": AttributeError,
        "StopIteration": StopIteration, "RuntimeError": RuntimeError,
        "FileNotFoundError": FileNotFoundError, "OSError": OSError,
        "IOError": IOError, "ZeroDivisionError": ZeroDivisionError,
        "AssertionError": AssertionError, "ImportError": ImportError,
        "NotImplementedError": NotImplementedError, "NameError": NameError,
        "SyntaxError": SyntaxError, "IndentationError": IndentationError,
        "KeyboardInterrupt": KeyboardInterrupt, "SystemExit": SystemExit,
        "MemoryError": MemoryError, "RecursionError": RecursionError,
        "OverflowError": OverflowError, "ArithmeticError": ArithmeticError,
        "LookupError": LookupError,
    }

    # 3. Безопасный open() — только в разрешённые директории
    ALLOWED_DIRS = [download_dir, processed_dir]
    _real_open = open
    def _safe_open(file, mode="r", *args, **kwargs):
        try:
            resolved = str(pathlib.Path(file).resolve())
        except Exception:
            raise PermissionError(f"Access denied: invalid path '{file}'")
        for d in ALLOWED_DIRS:
            if resolved.startswith(d):
                return _real_open(file, mode, *args, **kwargs)
        raise PermissionError(f"Access denied: '{file}' not in allowed directories")

    safe_builtins["open"] = _safe_open

    # 4. Импорт разрешённых модулей
    import pandas as pd
    import numpy as np
    import re, datetime, math, collections, itertools, statistics, copy

    # 5. Формируем namespace
    namespace = {
        "__builtins__": safe_builtins,
        "pd": pd, "pandas": pd, "np": np, "numpy": np,
        "input_path": input_path,
        "output_path": output_path,
        "output_dir": output_dir,
        "re": re, "datetime": datetime, "math": math,
        "collections": collections, "itertools": itertools,
        "statistics": statistics, "copy": copy,
    }

    # Пробуем импортировать опциональные модули
    for mod_name in ("openpyxl", "docx", "xlwt", "odf"):
        try:
            namespace[mod_name] = __import__(mod_name)
        except ImportError:
            pass

    # 6. Читаем и выполняем код
    code_lines = sys.stdin.read()
    redirected_out = StringIO()
    redirected_err = StringIO()
    old_out, old_err = sys.stdout, sys.stderr

    try:
        sys.stdout = redirected_out
        sys.stderr = redirected_err
        exec(code_lines, namespace)
        print("__EXIT_SUCCESS__", flush=True)
    except Exception:
        traceback.print_exc(file=redirected_err)
        print("__EXIT_FAILURE__", flush=True)
    finally:
        sys.stdout = old_out
        sys.stderr = old_err
        # Выводим captured output
        sys.stdout.write(redirected_out.getvalue())
        sys.stderr.write(redirected_err.getvalue())
""")

# Блок-лист опасных паттернов в коде (дополнительный слой защиты)
_BLOCKED_PATTERNS = [
    "subprocess", "shutil", "socket", "ctypes",
    "requests", "urllib", "http.client", "httpx", "aiohttp",
    "cryptography", "signal", "__builtins__",
    "__subclasses__", "__mro__", "__globals__", "__code__",
]


class CodeExecutor:
    """Исполняет Python-код в отдельном subprocess с ограниченным sandbox."""

    def __init__(self):
        from config import config as _cfg
        self._download_dir = str(Path(_cfg.DOWNLOAD_DIR).resolve())
        self._processed_dir = str(Path(_cfg.PROCESSED_DIR).resolve())

    def _check_blocked_patterns(self, code: str) -> Optional[str]:
        """Проверить код на опасные паттерны. Вернуть сообщение об ошибке или None."""
        for pattern in _BLOCKED_PATTERNS:
            if pattern in code:
                return f"⛔ Блокирован опасный код: найден паттерн '{pattern}'"
        return None

    def execute(self, code: str, input_path: str, output_path: str) -> dict:
        """
        Выполнить Python-код в отдельном subprocess с sandbox-ограничениями.

        Args:
            code: Python-код для выполнения.
            input_path: Путь к входному файлу.
            output_path: Путь для сохранения результата.

        Returns:
            dict с ключами: success, stdout, stderr, output_path
        """
        result = {
            "success": False,
            "stdout": "",
            "stderr": "",
            "output_path": output_path,
        }

        # === Проверка на опасные паттерны ===
        blocked = self._check_blocked_patterns(code)
        if blocked:
            result["stderr"] = blocked
            return result

        output_dir = str(Path(output_path).parent)

        # === Конфиг для sandbox-процесса ===
        sandbox_cfg = {
            "download_dir": self._download_dir,
            "processed_dir": self._processed_dir,
            "input_path": input_path,
            "output_path": output_path,
            "output_dir": output_dir,
        }
        config_json = json.dumps(sandbox_cfg)

        # === Запуск subprocess ===
        proc = subprocess.Popen(
            [sys.executable, "-c", _SANDBOX_BOOTSTRAP],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        try:
            # Пишем конфиг (первая строка) + код (остальной stdin)
            input_data = config_json + "\n" + code
            stdout_raw, stderr_raw = proc.communicate(input=input_data, timeout=120)
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout_raw, stderr_raw = proc.communicate()
            result["success"] = False
            result["stdout"] = stdout_raw or ""
            result["stderr"] = (stderr_raw or "") + "\n⏱️ Превышено время выполнения кода (120 сек). Процесс принудительно завершён."
            return result

        # === Парсим результат ===
        stdout_lines = stdout_raw.split("\n")
        stderr_combined = stderr_raw or ""

        # Ищем маркер завершения в stdout
        success_marker = "__EXIT_SUCCESS__"
        failure_marker = "__EXIT_FAILURE__"

        clean_stdout_lines = [l for l in stdout_lines if l not in (success_marker, failure_marker)]
        clean_stdout = "\n".join(clean_stdout_lines).strip()

        if success_marker in stdout_raw:
            result["success"] = True
            result["stdout"] = clean_stdout
            result["stderr"] = stderr_combined.strip()
        elif failure_marker in stdout_raw:
            result["success"] = False
            result["stdout"] = clean_stdout
            result["stderr"] = stderr_combined.strip() or "Код завершился с ошибкой."
        else:
            # Нет маркера — вероятно, процесс был прерван
            result["success"] = False
            result["stdout"] = clean_stdout
            result["stderr"] = stderr_combined.strip() or "Процесс завершился нештатно."

        # Проверяем, существует ли output файл
        if not Path(output_path).exists():
            result["stderr"] += "\n[WARNING] Файл результата не был создан по указанному пути."

        return result

    def analyze_file(self, file_path: str) -> dict:
        """Быстрый анализ файла: структура, типы, первые строки."""
        result = {"error": None, "info": {}}
        path = Path(file_path)
        ext = path.suffix.lower()

        try:
            if ext in (".xlsx", ".xls", ".ods"):
                excel_file = pd.ExcelFile(file_path)
                sheets = {}
                for sheet in excel_file.sheet_names:
                    df = pd.read_excel(file_path, sheet_name=sheet)
                    sheets[sheet] = {
                        "rows": len(df),
                        "cols": len(df.columns),
                        "columns": list(df.columns),
                        "dtypes": {c: str(df[c].dtype) for c in df.columns},
                        "head": df.head(3).to_dict(orient="records"),
                    }
                result["info"] = {
                    "type": "excel",
                    "sheets": sheets,
                    "sheet_count": len(sheets),
                }

            elif ext == ".csv":
                df = pd.read_csv(file_path)
                result["info"] = {
                    "type": "csv",
                    "rows": len(df),
                    "cols": len(df.columns),
                    "columns": list(df.columns),
                    "dtypes": {c: str(df[c].dtype) for c in df.columns},
                    "head": df.head(3).to_dict(orient="records"),
                }

        except Exception as e:
            result["error"] = str(e)

        return result


code_executor = CodeExecutor()
