"""
Безопасный исполнитель Python-кода.
Выполняет код в ОТДЕЛЬНОМ ПРОЦЕССЕ с ограниченными builtins и без доступа к os и подпроцессам.
"""

import multiprocessing
import sys
import traceback
from io import StringIO
from pathlib import Path
from typing import Optional

import pandas as pd


# ══════════════════════════════════════════════════════════════════
# Ограниченный набор builtins — ничего опасного
# ══════════════════════════════════════════════════════════════════
# Умышленно удалены: exec, eval, compile, __import__, input,
# globals, locals, vars, dir, open (заменяем своим), breakpoint, help
# ══════════════════════════════════════════════════════════════════
_SAFE_BUILTINS: dict[str, object] = {
    # Константы
    "True": True, "False": False, "None": None,
    "Ellipsis": Ellipsis, "NotImplemented": NotImplemented,
    # Фабрики типов
    "bool": bool, "int": int, "float": float, "complex": complex,
    "str": str, "bytes": bytes, "bytearray": bytearray,
    "list": list, "dict": dict, "tuple": tuple, "set": set, "frozenset": frozenset,
    "object": object, "type": type, "slice": slice,
    # Итерация / последовательности
    "range": range, "enumerate": enumerate, "zip": zip, "map": map, "filter": filter,
    "reversed": reversed, "sorted": sorted, "iter": iter, "next": next,
    "len": len, "min": min, "max": max, "sum": sum, "any": any, "all": all,
    # Математика / строки
    "abs": abs, "round": round, "pow": pow, "divmod": divmod,
    "ord": ord, "chr": chr, "repr": repr, "ascii": ascii, "format": format,
    "hash": hash, "id": id, "isinstance": isinstance, "issubclass": issubclass,
    "hasattr": hasattr, "getattr": getattr, "setattr": setattr, "delattr": delattr,
    "callable": callable, "staticmethod": staticmethod, "classmethod": classmethod,
    "property": property, "super": super,
    "print": print,
    # Исключения
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
    "isinstance": isinstance, "issubclass": issubclass,
}

# Удалены: exec, eval, compile, __import__, input, globals, locals, vars, dir,
# open (заменяем ниже), breakpoint, help, memoryview, __build_class__


class _RestrictedOpen:
    """Безопасная обёртка для open() — только в разрешённые директории."""

    ALLOWED_DIRS: list[str] = []

    @classmethod
    def configure(cls, download_dir: str, processed_dir: str):
        cls.ALLOWED_DIRS = [
            str(Path(download_dir).resolve()),
            str(Path(processed_dir).resolve()),
        ]

    @staticmethod
    def restricted_open(file, mode="r", *args, **kwargs):
        """open() только для файлов внутри download/processed."""
        try:
            resolved = str(Path(file).resolve())
        except Exception:
            raise PermissionError(f"⛔ Доступ запрещён: неверный путь '{file}'")

        allowed = False
        for d in _RestrictedOpen.ALLOWED_DIRS:
            if resolved.startswith(d):
                allowed = True
                break

        if not allowed:
            raise PermissionError(
                f"⛔ Доступ запрещён: файл '{file}' вне разрешённых директорий"
            )

        builtins_open = __builtins__["open"] if isinstance(__builtins__, dict) else __builtins__.open
        return builtins_open(file, mode, *args, **kwargs)


# Блок-лист опасных паттернов в коде (дополнительный слой защиты)
_BLOCKED_PATTERNS = [
    # Shell / subprocess (даже если каким-то чудом модуль появится)
    "subprocess",
    "shutil",
    "socket",
    "ctypes",
    # Сетевые запросы
    "requests",
    "urllib",
    "http.client",
    "httpx",
    "aiohttp",
    # Криптография (не нужна для работы с данными)
    "cryptography",
    # Системные вызовы
    "signal",
    "syscalls",
    "ptrace",
    # Обход ограничений
    "__builtins__",
    "__subclasses__",
    "__mro__",
    "__globals__",
    "__code__",
]


def _execute_in_process(
    code: str,
    namespace: dict,
    result_queue: multiprocessing.Queue,
    stdout_queue: multiprocessing.Queue,
    stderr_queue: multiprocessing.Queue,
):
    """
    Выполнить код в ДОЧЕРНЕМ ПРОЦЕССЕ.
    Эта функция запускается в отдельном процессе через multiprocessing.Process.
    """
    # Подсовываем ограниченные builtins
    safe_builtins = dict(_SAFE_BUILTINS)
    # Наш безопасный open
    safe_builtins["open"] = _RestrictedOpen.restricted_open
    namespace["__builtins__"] = safe_builtins

    old_stdout = sys.stdout
    old_stderr = sys.stderr
    redirected_out = StringIO()
    redirected_err = StringIO()

    try:
        sys.stdout = redirected_out
        sys.stderr = redirected_err
        exec(code, namespace)
        stdout_queue.put(redirected_out.getvalue())
        stderr_queue.put(redirected_err.getvalue())
        result_queue.put(True)
    except Exception as e:
        stdout_queue.put(redirected_out.getvalue())
        stderr_queue.put(redirected_err.getvalue() + "\n" + traceback.format_exc())
        result_queue.put(False)
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr


class CodeExecutor:
    """Исполняет Python-код в отдельном процессе с ограниченным sandbox."""

    # Разрешённые модули (без os, subprocess, socket и т.д.)
    SAFE_MODULES: dict[str, object | None] = {
        "pandas": pd,
        "pd": pd,
        "numpy": None,
        "np": None,
        "openpyxl": None,
        "docx": None,
        "json": __import__("json"),
        "re": __import__("re"),
        "datetime": __import__("datetime"),
        "math": __import__("math"),
        "collections": __import__("collections"),
        "itertools": __import__("itertools"),
        "statistics": __import__("statistics"),
        "pathlib": __import__("pathlib"),
        "copy": __import__("copy"),
        "typing": __import__("typing"),
        "os.path": __import__("os").path,  # только os.path, не os целиком
    }
    # ⚠️  Модуль `os` НЕ передаётся — только `os.path` для работы с путями.
    #     Это предотвращает os.system(), os.popen(), os.spawn*() и т.д.

    def __init__(self):
        self._lazy_modules: dict[str, bool] = {}
        from config import config as _cfg
        _RestrictedOpen.configure(_cfg.DOWNLOAD_DIR, _cfg.PROCESSED_DIR)

    def _load_lazy(self, name: str, import_name: str):
        """Ленивая загрузка модуля."""
        if name not in self._lazy_modules or not self._lazy_modules[name]:
            try:
                module = __import__(import_name)
                self.SAFE_MODULES[name] = module
                self._lazy_modules[name] = True
            except ImportError:
                self._lazy_modules[name] = False

    def _ensure_writers(self):
        """Подгрузить модули для записи разных форматов."""
        self._load_lazy("xlwt", "xlwt")
        self._load_lazy("odf", "odf")
        self._load_lazy("xml.etree.ElementTree", "xml.etree.ElementTree")

    def execute(self, code: str, input_path: str, output_path: str) -> dict:
        """
        Выполнить Python-код в изолированном процессе.

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

        # === Дополнительная проверка на опасные паттерны ===
        for pattern in _BLOCKED_PATTERNS:
            if pattern in code:
                result["stderr"] = f"⛔ Блокирован опасный код: найден паттерн '{pattern}'"
                return result

        # Подгрузка модулей
        self._ensure_writers()
        self._load_lazy("numpy", "numpy")
        self._load_lazy("np", "numpy")
        self._load_lazy("openpyxl", "openpyxl")
        self._load_lazy("docx", "docx")

        # Пространство имён для exec()
        output_dir = str(Path(output_path).parent)
        namespace: dict = {
            **self.SAFE_MODULES,
            "input_path": input_path,
            "output_path": output_path,
            "output_dir": output_dir,
            "pd": pd,
        }

        if "numpy" in namespace and namespace["numpy"] is not None:
            namespace["np"] = namespace["numpy"]

        # === Запуск в отдельном процессе (реально убивается по таймауту) ===
        result_queue: multiprocessing.Queue = multiprocessing.Queue()
        stdout_queue: multiprocessing.Queue = multiprocessing.Queue()
        stderr_queue: multiprocessing.Queue = multiprocessing.Queue()

        proc = multiprocessing.Process(
            target=_execute_in_process,
            args=(code, namespace, result_queue, stdout_queue, stderr_queue),
        )
        proc.start()
        proc.join(timeout=120)  # 2 min timeout

        try:
            if proc.is_alive():
                proc.terminate()
                proc.join(timeout=5)
                if proc.is_alive():
                    proc.kill()
                    proc.join()
                result["success"] = False
                result["stderr"] = "⏱️ Превышено время выполнения кода (120 сек). Процесс принудительно завершён."
            else:
                success = result_queue.get(timeout=2)
                stdout_val = stdout_queue.get(timeout=2)
                stderr_val = stderr_queue.get(timeout=2)
                result["success"] = success
                result["stdout"] = stdout_val
                result["stderr"] = stderr_val
        except Exception as e:
            result["success"] = False
            result["stderr"] = traceback.format_exc()

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
