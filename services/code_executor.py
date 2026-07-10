"""
Безопасный исполнитель Python-кода.
Выполняет код в ОТДЕЛЬНОМ ПРОЦЕССЕ (subprocess) с ограниченными builtins
и без доступа к os, subprocess, сети и файловой системе вне разрешённых папок.
"""

import ast
import json
import logging
import subprocess
import sys
import uuid
from pathlib import Path

import pandas as pd

from services.sandbox_bootstrap import _SANDBOX_BOOTSTRAP, _SANDBOX_BOOTSTRAP_FILE
from utils.helpers import sanitize_log
from utils.profiler_decorator import profiled

logger = logging.getLogger(__name__)

# Имя Docker-образа для sandbox-контейнера
DOCKER_SANDBOX_IMAGE = "tg_sup_ai-sandbox:latest"

# Параметры запуска Docker-контейнера (CPU, memory, network)
DOCKER_RUN_ARGS = [
    "--rm",                          # удалить контейнер после выполнения
    "--network", "none",             # без сетевого доступа
    "--read-only",                   # файловая система только для чтения
    "--tmpfs", "/tmp:size=100m,noexec,nosuid",  # /tmp в памяти, без exec
    "--memory", "512m",              # лимит RAM
    "--cpus", "1",                   # лимит CPU (1 ядро)
    "--security-opt", "no-new-privileges:true",  # запрет повышения привилегий
    "--cap-drop", "ALL",             # удалить все capabilities
]

# Блок-лист опасных паттернов в коде (дополнительный слой защиты)
# ВНИМАНИЕ: это поиск подстроки, а не AST-анализ — защита от случайных/наивных попыток.
_BLOCKED_PATTERNS = [
    # Модули ОС / исполнение команд
    "subprocess", "shutil", "socket", "ctypes",
    "requests", "urllib", "http.client", "httpx", "aiohttp",
    # Криптография / сигналы
    "cryptography", "signal",
    # Обход sandbox через MRO и dunder-атрибуты
    # ⚠️ __class__ НЕ добавляем — слишком част в легитимном коде (obj.__class__.__name__ и т.п.)
    "__builtins__", "__subclasses__", "__mro__", "__globals__", "__code__",
    "__reduce__", "__reduce_ex__",
    # Динамическая компиляция/исполнение кода
    "compile(", "eval(", "exec(",
    # Сериализация с риском RCE
    "pickle", "marshal", "base64",
    # Встроенный отладчик
    "breakpoint",
]


class CodeExecutor:
    """Исполняет Python-код в отдельном процессе (subprocess или Docker-контейнер) с ограничениями."""

    # ✅ Whitelist разрешённых dunder-атрибутов.
    # Любой другой __attr__ блокируется на уровне AST
    # (это закрывает обходы через конкатенацию строк, getattr, __getattribute__).
    # ⚠️ ВНИМАНИЕ: __class__ — единственный dunder в whitelist, через который
    #    теоретически можно добраться до __bases__ и __subclasses__().
    #    Он разрешён только для легитимного кода (obj.__class__.__name__).
    #    НЕ добавляйте __bases__, __mro__, __subclasses__ в этот whitelist.
    _ALLOWED_DUNDERS = frozenset({
        "__class__", "__name__", "__dict__", "__init__",
        "__str__", "__repr__", "__len__", "__iter__",
        "__next__", "__getitem__", "__setitem__",
        "__enter__", "__exit__", "__contains__",
        "__add__", "__sub__", "__mul__", "__truediv__",
        "__eq__", "__ne__", "__lt__", "__gt__", "__le__", "__ge__",
        "__hash__", "__bool__", "__call__",
    })

    def __init__(self):
        from config import config as _cfg
        self._download_dir = str(Path(_cfg.DOWNLOAD_DIR).resolve())
        self._processed_dir = str(Path(_cfg.PROCESSED_DIR).resolve())
        self._docker_enabled = getattr(_cfg, 'DOCKER_ENABLED', True)
        self._execution_timeout = getattr(_cfg, 'EXECUTION_TIMEOUT', 120)

    @staticmethod
    def _is_dunder(name: str) -> bool:
        """Проверить, является ли имя dunder-атрибутом (__xxx__)."""
        return name.startswith("__") and name.endswith("__") and len(name) > 4

    def _check_code_ast(self, code: str) -> str | None:
        """
        AST-анализ кода с whitelist-подходом (один проход вместо четырёх).
        - Блокирует ЛЮБОЙ dunder-атрибут (__xxx__), кроме _ALLOWED_DUNDERS
        - Сканирует строковые константы, атрибуты, getattr, __getattribute__
        - Использует constant propagation для отслеживания значений переменных
        """
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return None

        const_map: dict[str, str] = {}
        import re as _re

        # Единый проход: constant propagation + все проверки за 1 walk()
        for node in ast.walk(tree):
            # Constant propagation: var = 'строка'
            if isinstance(node, ast.Assign):
                if (len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                        and isinstance(node.value, ast.Constant)
                        and isinstance(node.value.value, str)):
                    const_map[node.targets[0].id] = node.value.value

            # 1. Строковые константы: dunder-паттерны
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                for match in _re.finditer(r'__[a-zA-Z_]\w+__', node.value):
                    dunder = match.group()
                    if self._is_dunder(dunder) and dunder not in self._ALLOWED_DUNDERS:
                        return f"⛔ AST: строковая константа содержит запрещённый dunder '{dunder}'"

            # 2. Атрибуты obj.__xxx__
            if isinstance(node, ast.Attribute):
                if self._is_dunder(node.attr) and node.attr not in self._ALLOWED_DUNDERS:
                    return f"⛔ AST: запрещён доступ к атрибуту '{node.attr}'"

            # 3. getattr(obj, ...) — блокируем ВСЕ случаи, даже с разрешёнными dunder
            #    (getattr в safe_builtins удалён, но на уровне AST перехватываем на случай
            #     будущих изменений — defence in depth)
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "getattr" and len(node.args) >= 2):
                arg = node.args[1]
                val = None
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    val = arg.value
                elif isinstance(arg, ast.Name) and arg.id in const_map:
                    val = const_map[arg.id]
                # Блокируем ЛЮБОЙ getattr с dunder (включая разрешённые — defense in depth)
                if val and self._is_dunder(val):
                    return f"⛔ AST: getattr() с dunder-атрибутом '{val}'"
                # Также блокируем getattr, даже если второй аргумент — не-строковый
                # (перехват динамических вызовов, не ловимых constant propagation)
                if not isinstance(arg, (ast.Constant, ast.Name)):
                    return "⛔ AST: getattr() с динамическим именем атрибута"

            # 4. obj.__getattribute__('...')
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "__getattribute__" and len(node.args) >= 1):
                arg = node.args[0]
                val = None
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    val = arg.value
                elif isinstance(arg, ast.Name) and arg.id in const_map:
                    val = const_map[arg.id]
                if val and self._is_dunder(val) and val not in self._ALLOWED_DUNDERS:
                    return f"⛔ AST: __getattribute__() с запрещённым dunder '{val}'"

        return None

    def _check_blocked_patterns(self, code: str) -> str | None:
        """Проверить код на опасные паттерны. Вернуть сообщение об ошибке или None.

        Использует два слоя защиты:
        1. AST-анализ — обнаруживает опасные атрибуты даже при конкатенации строк
        2. Substring search — блокирует явные опасные паттерны
        """
        # Слой 1: AST-анализ (обход конкатенации строк)
        ast_blocked = self._check_code_ast(code)
        if ast_blocked:
            return ast_blocked

        # Слой 2: substring search (быстрый, для очевидных случаев)
        for pattern in _BLOCKED_PATTERNS:
            if pattern in code:
                return f"⛔ Блокирован опасный код: найден паттерн '{pattern}'"
        return None

    @staticmethod
    @profiled()
    def _sanitize_code(code: str) -> str:
        """
        Постобработка AI-кода: удаляем хардкоженные пути и лишние импорты,
        чтобы AI не переопределял переменные input_path / output_path.
        """
        import re
        lines = code.split("\n")
        cleaned = []
        for line in lines:
            stripped = line.strip()
            # Удаляем строки вида `input_path = '...'` или `output_path = "..."` (хардкод)
            if re.match(r'^(input_path|output_path|output_dir)\s*=\s*[\'"]', stripped):
                cleaned.append(f"# {stripped}  # (заменено sandbox-ом)")
                continue
            # Удаляем `import pandas as pd` — pd уже в namespace
            if re.match(r'^import\s+pandas', stripped):
                continue
            # Удаляем `from pandas import ...`
            if re.match(r'^from\s+pandas', stripped):
                continue
            # Удаляем `import numpy as np` — np уже в namespace
            if re.match(r'^import\s+numpy', stripped):
                continue
            cleaned.append(line)
        return "\n".join(cleaned)

    @profiled()
    def execute(self, code: str, input_path: str, output_path: str) -> dict:
        """
        Выполнить Python-код в изолированном процессе (subprocess или Docker).

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

        # === Постобработка AI-кода: убираем хардкоженные пути и дублирующие импорты ===
        code = self._sanitize_code(code)

        # ⚠️ Резолвим пути в абсолютные — subprocess может иметь другой CWD
        input_path_abs = str(Path(input_path).resolve())
        output_path_abs = str(Path(output_path).resolve())
        output_dir_abs = str(Path(output_path_abs).parent)

        # === Конфиг для sandbox-процесса ===
        sandbox_cfg = {
            "download_dir": self._download_dir,
            "processed_dir": self._processed_dir,
            "input_path": input_path_abs,
            "output_path": output_path_abs,
            "output_dir": output_dir_abs,
        }
        config_json = json.dumps(sandbox_cfg)

        # === Выбор способа выполнения ===
        if self._docker_enabled:
            return self._execute_in_docker(code, config_json, input_path_abs, output_path_abs, result)
        else:
            return self._execute_direct(code, config_json, result)

    def _execute_direct(self, code: str, config_json: str, result: dict) -> dict:
        """
        Выполнить код напрямую через subprocess (тот же интерпретатор Python).
        Используется когда Docker недоступен (DOCKER_ENABLED=false).
        """
        output_path = result["output_path"]

        logger.info("Режим: прямой subprocess (cwd=%s)", Path.cwd())

        proc = subprocess.Popen(
            [sys.executable, "-c", _SANDBOX_BOOTSTRAP],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=str(Path.cwd()),
        )

        try:
            input_data = config_json + "\n" + code
            stdout_raw, stderr_raw = proc.communicate(input=input_data, timeout=self._execution_timeout)
            logger.info("stdout (%d chars)", len(stdout_raw))
            logger.info("stderr (%d chars): %s", len(stderr_raw), sanitize_log(stderr_raw[:500]))
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout_raw, stderr_raw = proc.communicate()
            return self._parse_result(result, stdout_raw, stderr_raw, success=False,
                                       extra_stderr=f"\n⏱️ Превышено время выполнения кода ({self._execution_timeout} сек). Процесс принудительно завершён.")

        stdout_lines = stdout_raw.split("\n")
        stderr_combined = stderr_raw or ""
        success_marker = "__EXIT_SUCCESS__"
        failure_marker = "__EXIT_FAILURE__"

        clean_stdout_lines = [l for l in stdout_lines if l not in (success_marker, failure_marker)]
        clean_stdout = "\n".join(clean_stdout_lines).strip()

        if success_marker in stdout_raw:
            result = self._parse_result(result, clean_stdout, stderr_combined.strip(), success=True)
        elif failure_marker in stdout_raw:
            result = self._parse_result(result, clean_stdout, stderr_combined.strip() or "Код завершился с ошибкой.", success=False)
        else:
            result = self._parse_result(result, clean_stdout, stderr_combined.strip() or "Процесс завершился нештатно.", success=False)

        # Проверяем, существует ли output файл
        if Path(output_path).exists():
            logger.info("✅ Файл создан: %s", output_path)
        else:
            logger.warning("❌ Файл НЕ создан: %s", output_path)
            result["stderr"] += "\n[WARNING] Файл результата не был создан по указанному пути."

        return result

    def _execute_in_docker(self, code: str, config_json: str, input_path_abs: str, output_path_abs: str, result: dict) -> dict:
        """
        Выполнить код в Docker-контейнере с полной изоляцией:
        - Без сети (--network none)
        - Только чтение (--read-only)
        - Лимит RAM 512m, CPU 1 ядро
        - Без привилегий (--cap-drop ALL)
        - Монтируются только downloads/ и processed/

        На Windows: Docker Desktop монтирует Windows-пути (E:\\...) в Linux-контейнер.
        Внутри контейнера они видны как /data/downloads, /data/processed.
        """
        CONTAINER_MOUNT = "/data"
        output_path = result["output_path"]

        logger.info("Режим: Docker-контейнер (%s)", DOCKER_SANDBOX_IMAGE)
        logger.info("input=%s", sanitize_log(input_path_abs))
        logger.info("output=%s", sanitize_log(output_path_abs))

        # Конвертируем Windows-пути в Linux-пути внутри контейнера
        try:
            input_rel = str(Path(input_path_abs).relative_to(self._download_dir)).replace("\\", "/")
        except ValueError:
            input_rel = Path(input_path_abs).name  # fallback: только имя
        try:
            output_rel = str(Path(output_path_abs).relative_to(self._processed_dir)).replace("\\", "/")
        except ValueError:
            output_rel = Path(output_path_abs).name  # fallback: только имя

        container_input = f"{CONTAINER_MOUNT}/downloads/{input_rel}"
        container_output = f"{CONTAINER_MOUNT}/processed/{output_rel}"
        container_download_dir = f"{CONTAINER_MOUNT}/downloads"
        container_processed_dir = f"{CONTAINER_MOUNT}/processed"

        # Генерируем конфиг для sandbox с Linux-путями внутри контейнера
        container_cfg = {
            "download_dir": container_download_dir,
            "processed_dir": container_processed_dir,
            "input_path": container_input,
            "output_path": container_output,
            "output_dir": f"{CONTAINER_MOUNT}/processed",
        }
        container_config_json = json.dumps(container_cfg)

        logger.info("container_input=%s", container_input)
        logger.info("container_output=%s", container_output)

        # ════════════════════════════════════════════════════════════════
        # Вместо stdin (ломается на Windows → cp1252 vs UTF-8)
        # пишем ТОЛЬКО конфиг + код пользователя в файл.
        # Bootstrap выполняется через -c, читает файл через sys.argv[1].
        # ════════════════════════════════════════════════════════════════
        script_id = uuid.uuid4().hex
        script_rel = f"_sandbox_{script_id}.py"
        script_host = Path(self._processed_dir) / script_rel
        script_container = f"{CONTAINER_MOUNT}/processed/{script_rel}"

        # Файл: строка 1 = JSON-конфиг, остальное = код пользователя
        script_host.write_text(
            container_config_json + "\n" + code,
            encoding="utf-8",
        )
        logger.info("script_host=%s", script_host)

        docker_cmd = ["docker", "run"] + DOCKER_RUN_ARGS + [
            "-v", f"{self._download_dir}:{CONTAINER_MOUNT}/downloads:ro",
            "-v", f"{self._processed_dir}:{CONTAINER_MOUNT}/processed",
            DOCKER_SANDBOX_IMAGE,
            "-c", _SANDBOX_BOOTSTRAP_FILE,
            script_container,
        ]

        try:
            proc = subprocess.Popen(
                docker_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            stdout_raw_bytes, stderr_raw_bytes = proc.communicate(timeout=self._execution_timeout)
            stdout_raw = stdout_raw_bytes.decode("utf-8", errors="replace")
            stderr_raw = stderr_raw_bytes.decode("utf-8", errors="replace")
            logger.info("Docker stdout (%d chars)", len(stdout_raw))
            logger.info("Docker stderr (%d chars): %s", len(stderr_raw), sanitize_log(stderr_raw[:500]))

        except FileNotFoundError:
            result["stderr"] = "❌ Docker не найден. Установите Docker Desktop или отключите DOCKER_ENABLED в .env"
            return result
        except subprocess.TimeoutExpired:
            subprocess.run(["docker", "stop", "-t", "0"], capture_output=True)
            stdout_raw, stderr_raw = "", ""
            return self._parse_result(result, "", stderr_raw or "",
                                       extra_stderr=f"\n⏱️ Превышено время выполнения кода ({self._execution_timeout} сек). Контейнер остановлен.",
                                       success=False)
        except Exception as e:
            result["stderr"] = f"❌ Ошибка Docker: {type(e).__name__}: {e}"
            return result

        # Парсим результат
        stdout_lines = stdout_raw.split("\n")
        stderr_combined = stderr_raw or ""
        success_marker = "__EXIT_SUCCESS__"
        failure_marker = "__EXIT_FAILURE__"

        clean_stdout_lines = [l for l in stdout_lines if l not in (success_marker, failure_marker)]
        clean_stdout = "\n".join(clean_stdout_lines).strip()

        if success_marker in stdout_raw:
            result = self._parse_result(result, clean_stdout, stderr_combined.strip(), success=True)
        elif failure_marker in stdout_raw:
            result = self._parse_result(result, clean_stdout, stderr_combined.strip() or "Код завершился с ошибкой.", success=False)
        else:
            result = self._parse_result(result, clean_stdout, stderr_combined.strip() or "Контейнер завершился нештатно.", success=False)

        # Проверяем output-файл
        if Path(output_path).exists():
            logger.info("✅ Docker: файл создан: %s", output_path)
        else:
            logger.warning("❌ Docker: файл НЕ создан: %s", output_path)
            result["stderr"] += "\n[WARNING] Файл результата не был создан по указанному пути."

        return result

    @staticmethod
    def _parse_result(result: dict, stdout: str, stderr: str, success: bool, extra_stderr: str = "") -> dict:
        """Заполнить результат выполнения."""
        result["success"] = success
        result["stdout"] = stdout
        result["stderr"] = stderr + extra_stderr
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