# ═══════════════════════════════════════════════════════════
# ⚠️  ВНИМАНИЕ: Это ШАБЛОН конфигурации
# ═══════════════════════════════════════════════════════════
# Никогда не вставляйте реальные ключи в этот файл!
# Реальные секреты храните в файле .env (см. .env.example)
# Этот файл отслеживается git и попадает в публичный репозиторий.
# ═══════════════════════════════════════════════════════════

# --- AI Provider Configuration ---
# Выберите 'gemini' или 'openai'
AI_PROVIDER = "gemini"

# Gemini API Key (вставьте ваш ключ)
GEMINI_API_KEY = "YOUR_GEMINI_API_KEY"
GEMINI_MODEL = "gemini-1.5-flash-latest"  # Или ваша предпочитаемая модель

# OpenAI API Key (вставьте ваш ключ, если используется)
OPENAI_API_KEY = "YOUR_OPENAI_API_KEY"
OPENAI_MODEL = "gpt-4o"
OPENAI_MAX_TOKENS = 4096
OPENAI_TEMPERATURE = 0.2

# --- Telegram Bot Configuration ---
TELEGRAM_BOT_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"

# --- Bot Admin ---
# Список ID пользователей, которые являются администраторами
ADMIN_IDS = [123456789]

# --- File and Execution Settings ---
# Директории для хранения файлов
DOWNLOAD_DIR = "downloads"
PROCESSED_DIR = "processed"

# Максимальный размер файла в мегабайтах
MAX_FILE_SIZE_MB = 20

# Режим выполнения кода: 'local' или 'assistants' (для Gemini Sandbox)
EXECUTION_MODE = "local"

# --- Rate Limiting ---
RATE_LIMIT_REQUESTS_PER_MIN = 5
RATE_LIMIT_FILE_UPLOADS_PER_HOUR = 20