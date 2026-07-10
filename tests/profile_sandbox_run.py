"""
Скрипт для CI-профилирования: запускает code_executor + file_service
и сохраняет .prof файлы. Используется в .github/workflows/profile.yml.

Запуск:
    ENABLE_PROFILING=true python -m tests.profile_sandbox_run
"""

import os
import sys
import textwrap
from pathlib import Path

# Добавляем корень проекта в PYTHONPATH
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import config
from services.code_executor import code_executor
from services.file_service import file_service


def main():
    # Создаём тестовые файлы
    downloads = Path(config.DOWNLOAD_DIR) / "0"
    processed = Path(config.PROCESSED_DIR) / "0"
    downloads.mkdir(parents=True, exist_ok=True)
    processed.mkdir(parents=True, exist_ok=True)

    import pandas as pd

    # Тестовый Excel
    df = pd.DataFrame({"A": range(1000), "B": ["x"] * 1000, "C": [1.5] * 1000})
    xlsx_path = downloads / "test_input.xlsx"
    df.to_excel(str(xlsx_path), index=False)

    csv_path = downloads / "test_input.csv"
    df.to_csv(str(csv_path), index=False)

    # 1. Профилируем get_file_summary (холодный кэш)
    print("📊 Profile: get_file_summary (Excel)...")
    summary = file_service.get_file_summary(str(xlsx_path))
    print(f"   {summary[:80]}...")

    # 2. Профилируем get_file_summary (горячий кэш)
    print("📊 Profile: get_file_summary (cached)...")
    summary2 = file_service.get_file_summary(str(xlsx_path))
    print(f"   OK (cached)")

    # 3. Профилируем code_executor.execute
    code = textwrap.dedent("""\
        import pandas as pd
        df = pd.read_excel(input_path)
        df['C'] = df['A'] * 2
        df.to_excel(output_path, index=False)
        print(f"Обработано {len(df)} строк")
    """)
    print("📊 Profile: code_executor.execute...")
    result = code_executor.execute(
        code=code,
        input_path=str(xlsx_path),
        output_path=str(processed / "test_result.xlsx"),
    )
    print(f"   Success: {result['success']}, stdout: {result.get('stdout', '')[:100]}")

    # 4. Профилируем _check_code_ast (AST-анализ)
    print("📊 Profile: _check_code_ast...")
    code_executor._check_code_ast(code)

    print("\n✅ Профилирование завершено")
    print(f"📁 Смотри .prof файлы в: {config.PROFILE_DIR}/")


if __name__ == "__main__":
    main()
