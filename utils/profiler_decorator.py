"""
Профилировщик кода: декоратор @profiled + helpers.

Установка:
    pip install snakeviz   # визуализация .prof-файлов
    pip install yappi      # профилирование async-кода

Использование в коде:
    @profiled()
    def hot_function(...):
        ...

Включение: установить ENABLE_PROFILING=true в .env
Результат: profiles/<function_name>.prof — открыть snakeviz.
"""

import cProfile
import functools
import logging
import pstats
from pathlib import Path

from config import config

logger = logging.getLogger(__name__)

# Директория для .prof-файлов (создаётся автоматически)
PROFILE_DIR = Path(getattr(config, "PROFILE_DIR", "profiles"))


# ════════════════════════════════════════════════════════════════
# Декоратор @profiled — навешивается на синхронные функции
# ════════════════════════════════════════════════════════════════

def profiled(output_dir: str | Path | None = None):
    """
    Декоратор для профилирования функции.
    Работает только при ENABLE_PROFILING=true.

    Args:
        output_dir: Куда сохранять .prof (по умолчанию PROFILES_DIR).

    Пример:
        @profiled()
        def my_func():
            ...

        @profiled(output_dir="custom_profiles")
        def another_func():
            ...
    """
    output_path = Path(output_dir) if output_dir else PROFILE_DIR

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            if not config.ENABLE_PROFILING:
                return func(*args, **kwargs)

            output_path.mkdir(parents=True, exist_ok=True)
            profiler = cProfile.Profile()
            profiler.enable()
            try:
                return func(*args, **kwargs)
            finally:
                profiler.disable()
                stats = pstats.Stats(profiler)
                stats.sort_stats("cumulative")
                prof_file = output_path / f"{func.__name__}.prof"
                stats.dump_stats(str(prof_file))
                logger.info(
                    "📊 Профиль сохранён: %s (вызовов: %d)",
                    prof_file, stats.total_calls,
                )
        return wrapper
    return decorator


# ════════════════════════════════════════════════════════════════
# Обёртка profile_execution — для точечного профилирования
# ════════════════════════════════════════════════════════════════

def profile_execution(func, *args, output_dir: str | Path | None = None, **kwargs):
    """
    Обёртка для профилирования любой функции с сохранением результата.
    Работает всегда (не зависит от ENABLE_PROFILING).

    Пример:
        result = profile_execution(execute_code_in_sandbox, user_code, timeout=5)
        # → profiles/execute_code_in_sandbox.prof
    """
    output_path = Path(output_dir) if output_dir else PROFILE_DIR
    output_path.mkdir(parents=True, exist_ok=True)

    profiler = cProfile.Profile()
    profiler.enable()
    try:
        result = func(*args, **kwargs)
    finally:
        profiler.disable()

    stats = pstats.Stats(profiler)
    stats.sort_stats("cumulative")
    prof_file = output_path / f"{func.__name__}.prof"
    stats.dump_stats(str(prof_file))
    print(f"📊 Профиль сохранён: {prof_file}")
    return result


# ════════════════════════════════════════════════════════════════
# yappi — профилирование async-кода (aiogram / asyncio)
# ════════════════════════════════════════════════════════════════

def start_yappi(output_dir: str | Path | None = None):
    """
    Запустить yappi-профайлер для async-кода.
    Возвращает функцию stop(), которую нужно вызвать по завершении.

    Пример:
        stop_yappi = start_yappi()
        # ... работа бота ...
        stop_yappi()
    """
    output_path = Path(output_dir) if output_dir else PROFILE_DIR
    output_path.mkdir(parents=True, exist_ok=True)

    try:
        import yappi
    except ImportError:
        logger.warning("yappi не установлен: pip install yappi")
        return lambda: None

    yappi.set_clock_type("wall")  # wall-clock time (реальное время)
    yappi.start()

    def stop():
        yappi.stop()
        stats = yappi.get_func_stats()
        prof_file = output_path / "async_profile.pstat"
        stats.save(str(prof_file), type="pstat")
        logger.info("📊 Async-профиль сохранён: %s", prof_file)

        # Показываем топ-10 по общему времени
        stats.sort("ttot", sort_order="desc")
        print("\n📊 Топ-10 функций по общему времени:")
        stats.print_all(limit=10)

    return stop
