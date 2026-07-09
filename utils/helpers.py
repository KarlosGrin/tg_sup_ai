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
    Найти созданный выходной файл. Только по точному пути — никакого
    «поиска похожих», чтобы не перепутать файлы разных пользователей.
    """
    if os.path.exists(output_path):
        return output_path
    return None
