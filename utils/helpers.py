"""
Вспомогательные утилиты.
"""

import os
import re
from pathlib import Path


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


_LOGGED_API_KEY_PATTERNS = [
    r'(sk-[A-Za-z0-9_-]{20,})',         # OpenAI ключи (sk-proj-, sk-, sk-svcacct-)
    r'(org-[A-Za-z0-9_-]{20,})',        # OpenAI Organization ID
    r'(AIza[0-9A-Za-z_-]{35})',         # Gemini ключи
    r'(xox[bpsa]-[A-Za-z0-9-]{10,})',   # Slack-подобные
    r'(ghp_[A-Za-z0-9_]{36})',          # GitHub Personal Access Token
    r'(gho_[A-Za-z0-9_]{36})',          # GitHub OAuth Access Token
    r'(github_pat_[A-Za-z0-9_-]{80,})', # GitHub Fine-Grained Token
    r'(token:[A-Za-z0-9_-]{40,})',      # Generic token:...
    r'(api[-_]?key[-_]?[=:].{8,})',     # Generic api_key/api-key pattern
]


def sanitize_log(text: str) -> str:
    """
    Очистить строку от потенциально опасных данных для логов:
    - API-ключи
    - Токены
    Заменяет их на '***REDACTED***'.
    """
    import re
    for pattern in _LOGGED_API_KEY_PATTERNS:
        text = re.sub(pattern, r'***REDACTED***', text)
    return text


def find_output_file(output_path: str, base_dir: str = "processed") -> str | None:
    """
    Найти созданный выходной файл. Только по точному пути — никакого
    «поиска похожих», чтобы не перепутать файлы разных пользователей.
    """
    if os.path.exists(output_path):
        return output_path
    return None
