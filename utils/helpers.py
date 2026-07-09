"""
Вспомогательные утилиты.
"""

import re
import os
from pathlib import Path
from typing import Optional


def clean_filename(filename: str) -> str:
    """Очистить имя файла от недопустимых символов."""
    return re.sub(r'[<>:"/\\|?*]', "_", filename)


def format_size(size_bytes: int) -> str:
    """Форматировать размер файла в человекочитаемый вид."""
    for unit in ["Б", "КБ", "МБ", "ГБ"]:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} ТБ"


def ensure_dir(path: str) -> Path:
    """Создать директорию, если не существует."""
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def sanitize_for_markdown(text: str) -> str:
    """
    Очистить текст от незакрытых Markdown-символов,
    чтобы Telegram не выдавал ошибку "Can't parse entities".
    Удаляет незакрытые ** * _ ` 
    """
    # Удаляем незакрытые ** (две звёздочки без закрытия)
    lines = text.split("\n")
    cleaned = []
    for line in lines:
        # Считаем количество ** в строке
        bold_count = line.count("**")
        if bold_count % 2 != 0:
            # Нечётное количество ** — закрываем
            line += "**"
        italic_count = line.count("_")
        if italic_count % 2 != 0:
            line += "_"
        # Экранируем обратную кавычку, если нечётное количество
        code_count = line.count("`")
        if code_count % 2 != 0:
            line += "`"
        cleaned.append(line)
    return "\n".join(cleaned)


def find_output_file(output_path: str, base_dir: str = "processed") -> Optional[str]:
    """
    Найти созданный выходной файл. Если файл не найден по точному пути,
    ищет похожие файлы в директории.
    """
    if os.path.exists(output_path):
        return output_path

    output_dir = Path(base_dir)
    if not output_dir.exists():
        return None

    # Ищем по имени (без UUID)
    output_name = Path(output_path).name
    parts = output_name.split("_", 1)
    search_name = parts[1] if len(parts) > 1 else output_name
    base_name = os.path.splitext(search_name)[0]

    candidates = list(output_dir.glob(f"*{base_name}*"))
    if candidates:
        return str(max(candidates, key=os.path.getmtime))

    return None
