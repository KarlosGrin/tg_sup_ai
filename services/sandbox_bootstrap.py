"""
Bootstrap-скрипты для изолированного выполнения AI-сгенерированного Python-кода.

Содержит две версии:
- _SANDBOX_BOOTSTRAP — для прямого subprocess (stdin-based)
- _SANDBOX_BOOTSTRAP_FILE — для Docker (file-based)

Обе версии генерируются из единой функции _build_sandbox_bootstrap(),
чтобы избежать рассинхронизации при правках безопасности.
"""

import textwrap
from typing import Literal

# Шаблон bootstrap-скрипта. {config_source} заменяется на код чтения конфига.
_SANDBOX_TEMPLATE = textwrap.dedent("""\
    import sys, json, pathlib, traceback
    from io import StringIO

    # 1. Читаем конфиг
    {config_source}

    download_dir = cfg["download_dir"]
    processed_dir = cfg["processed_dir"]
    input_path = cfg["input_path"]
    output_path = cfg["output_path"]
    output_dir = cfg["output_dir"]

    # 2. Импорт разрешённых модулей (ДО ограничения builtins — нужен __import__)
    import pandas as pd
    import numpy as np
    import re, datetime, math, collections, itertools, statistics, copy
    import json as _json_mod

    # 3. Белый список модулей, которые можно импортировать из пользовательского кода
    # ⚠️ Никогда не добавляйте сюда "os", "subprocess", "shutil", "socket" и т.д.
    _ALLOWED_IMPORTS = {{
        "pandas", "numpy", "openpyxl", "docx", "xlwt", "odf",
        "re", "datetime", "math", "collections", "itertools", "statistics", "copy",
        "json", "pathlib", "io", "typing",
        "xml", "xml.etree", "xml.etree.ElementTree",
    }}

    _real_import = __import__
    def _safe_import(name, *args, **kwargs):
        # Прямое совпадение
        if name in _ALLOWED_IMPORTS:
            return _real_import(name, *args, **kwargs)
        # Субмодули разрешённых модулей (openpyxl.styles, json.decoder и т.д.)
        for allowed in _ALLOWED_IMPORTS:
            if name.startswith(allowed + "."):
                return _real_import(name, *args, **kwargs)
        raise ImportError(f"⛔ Импорт модуля '{{name}}' запрещён по соображениям безопасности")

    # 4. Ограниченные builtins (с безопасным __import__ и open)
    # ⚠️ getattr НАМЕРЕННО исключён — он позволяет обходить AST-анализ через
    #    динамические имена атрибутов, не отслеживаемые constant propagation.
    #    Используйте direct attribute access (obj.attr) вместо getattr().
    safe_builtins = {{
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
        "hasattr": hasattr,
        "setattr": setattr, "delattr": delattr,
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
        "__import__": _safe_import,
    }}

    # 5. Безопасный open() — только в разрешённые директории
    ALLOWED_DIRS = [download_dir, processed_dir]
    _real_open = open
    def _safe_open(file, mode="r", *args, **kwargs):
        try:
            resolved = str(pathlib.Path(file).resolve())
        except Exception:
            raise PermissionError(f"Access denied: invalid path '{{file}}'")
        for d in ALLOWED_DIRS:
            if resolved.startswith(d):
                return _real_open(file, mode, *args, **kwargs)
        raise PermissionError(f"Access denied: '{{file}}' not in allowed directories")

    safe_builtins["open"] = _safe_open

    # 6. Формируем namespace
    namespace = {{
        "__builtins__": safe_builtins,
        "pd": pd, "pandas": pd, "np": np, "numpy": np,
        "input_path": input_path,
        "output_path": output_path,
        "output_dir": output_dir,
        "re": re, "datetime": datetime, "math": math,
        "collections": collections, "itertools": itertools,
        "statistics": statistics, "copy": copy,
    }}

    # Пробуем импортировать опциональные модули
    for mod_name in ("openpyxl", "docx", "xlwt", "odf"):
        try:
            namespace[mod_name] = __import__(mod_name)
        except ImportError:
            pass

    # 7. Читаем и выполняем код
    {code_source}

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


def _build_sandbox_bootstrap(config_source: Literal["stdin", "file"]) -> str:
    """Собрать sandbox bootstrap-скрипт для указанного источника конфига.

    Args:
        config_source: "stdin" — конфиг из первой строки stdin, код из остатка stdin.
                       "file" — конфиг из первой строки файла (sys.argv[1]), код из остатка файла.

    Returns:
        Готовый Python-скрипт для exec() в sandbox-процессе.
    """
    if config_source == "stdin":
        config_block = textwrap.dedent("""\
            # Читаем конфиг из stdin (первая строка — JSON)
            config_line = sys.stdin.readline()
            cfg = json.loads(config_line)""")
        code_block = textwrap.dedent("""\
            # Читаем код из stdin (всё после первой строки)
            code_lines = sys.stdin.read()""")
    elif config_source == "file":
        config_block = textwrap.dedent("""\
            # Читаем конфиг из первой строки файла (путь в sys.argv[1])
            script_path = sys.argv[1]
            with open(script_path, "r") as _sf:
                config_line = _sf.readline().strip()
            cfg = json.loads(config_line)""")
        code_block = textwrap.dedent("""\
            # Читаем код пользователя из файла (всё после первой строки)
            with open(script_path, "r") as _sf:
                _sf.readline()  # пропускаем строку конфига
                code_lines = _sf.read()""")
    else:
        raise ValueError(f"Unknown config_source: {config_source!r}")

    return _SANDBOX_TEMPLATE.format(config_source=config_block, code_source=code_block)


# Синглтоны — вычисляются один раз при импорте
_SANDBOX_BOOTSTRAP = _build_sandbox_bootstrap("stdin")
_SANDBOX_BOOTSTRAP_FILE = _build_sandbox_bootstrap("file")