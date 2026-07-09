"""
Безопасный исполнитель Python-кода.
Генерирует изолированное окружение для выполнения кода, сгенерированного AI.
"""

import sys
import time
import traceback
from io import StringIO
from pathlib import Path
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, TimeoutError

import pandas as pd


# Блок-лист опасных паттернов в коде
BLOCKED_PATTERNS = [
    "os.system",
    "os.popen",
    "subprocess.",
    "shutil.rmtree",
    "__import__",
    "eval(",
    "exec(",
    "__builtins__",
    "socket.",
    "ctypes.",
    "requests.",
    "urllib.",
]


class CodeExecutor:
    """Исполняет Python-код в изолированном пространстве имён."""

    # Разрешённые модули
    SAFE_MODULES = {
        "pandas": pd,
        "pd": pd,
        "numpy": None,
        "np": None,
        "openpyxl": None,
        "docx": None,
        "json": __import__("json"),
        "os": __import__("os"),
        "re": __import__("re"),
        "datetime": __import__("datetime"),
        "math": __import__("math"),
        "collections": __import__("collections"),
        "itertools": __import__("itertools"),
        "statistics": __import__("statistics"),
        "pathlib": __import__("pathlib"),
        "copy": __import__("copy"),
        "typing": __import__("typing"),
    }

    def __init__(self):
        self._lazy_modules: dict[str, bool] = {}

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
        self._load_lazy("xlwt", "xlwt")       # .xls
        self._load_lazy("odf", "odf")          # .ods
        self._load_lazy("xml.etree.ElementTree", "xml.etree.ElementTree")  # .xml

    def execute(self, code: str, input_path: str, output_path: str) -> dict:
        """
        Выполнить Python-код в изолированном окружении.

        Args:
            code: Python-код для выполнения.
            input_path: Путь к входному файлу.
            output_path: Путь для сохранения результата.

        Returns:
            dict с ключами:
                - success: bool
                - stdout: str (вывод кода)
                - stderr: str (ошибки, если есть)
                - output_path: str (путь к результату)
        """
        result = {
            "success": False,
            "stdout": "",
            "stderr": "",
            "output_path": output_path,
        }

        # Проверка кода на опасные паттерны
        for pattern in BLOCKED_PATTERNS:
            if pattern in code:
                result["stderr"] = f"⛔ Блокирован опасный код: найден паттерн '{pattern}'"
                return result

        # Подгрузка модулей для записи всех форматов
        self._ensure_writers()

        # Ленивая загрузка numpy
        self._load_lazy("numpy", "numpy")
        self._load_lazy("np", "numpy")
        self._load_lazy("openpyxl", "openpyxl")
        self._load_lazy("docx", "docx")

        # Подготавливаем пространство имён
        output_dir = str(Path(output_path).parent)
        namespace = {
            **self.SAFE_MODULES,
            "input_path": input_path,
            "output_path": output_path,
            "output_dir": output_dir,
            "pd": pd,
        }

        # Добавляем numpy если загрузился
        if "numpy" in namespace and namespace["numpy"] is not None:
            namespace["np"] = namespace["numpy"]

        # Перехватываем stdout
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        redirected_out = StringIO()
        redirected_err = StringIO()

        def _run():
            sys.stdout = redirected_out
            sys.stderr = redirected_err
            try:
                exec(code, namespace)
                return True, redirected_out.getvalue(), redirected_err.getvalue()
            except Exception as e:
                return False, redirected_out.getvalue(), redirected_err.getvalue() + "\n" + traceback.format_exc()

        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                fut = executor.submit(_run)
                success, stdout_val, stderr_val = fut.result(timeout=120)  # 2 min timeout
            result["success"] = success
            result["stdout"] = stdout_val
            result["stderr"] = stderr_val
        except TimeoutError:
            result["success"] = False
            result["stderr"] = "⏱️ Превышено время выполнения кода (120 сек)."
        except Exception as e:
            result["success"] = False
            result["stderr"] = traceback.format_exc()
        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr

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
