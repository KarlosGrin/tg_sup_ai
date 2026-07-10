# Contributing to tg_sup_ai

Спасибо за интерес к проекту! Вот несколько правил, которые помогут поддерживать код в порядке.

## 🚀 Быстрый старт

```bash
# Клонировать
git clone <url>
cd tg_sup_ai

# Виртуальное окружение
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate

# Dev-зависимости
pip install -r requirements-dev.txt

# Настроить .env
cp .env.example .env
# отредактировать .env (BOT_TOKEN, API-ключи)
```

## ✅ Перед отправкой PR

1. **Линтер**
   ```bash
   ruff check .
   ```

2. **Тесты**
   ```bash
   python -m pytest tests/ -v --tb=short
   ```
   Все тесты должны быть зелёными.

3. **Импорт без ошибок**
   ```bash
   python -c "import main"
   ```

## 🔒 Security-критичный код

**Файл `services/code_executor.py`** содержит логику песочницы (блок-листы, AST-проверка, Docker-режим).
Изменения в нём требуют **отдельного ревью**. Не смешивайте правки `code_executor.py` с другими изменениями в одном PR.

## 📝 Стандарты кода

- **Язык**: английский для имён переменных/функций, русский для комментариев и docstring
- **Стиль**: ruff (pyproject.toml), линия 120 символов
- **Типизация**: приветствуется (mypy), но не обязательна
- **Коммиты**: понятные сообщения на русском или английском

## 📁 Структура handlers/

После рефакторинга хендлеры разбиты по роутерам:

```
handlers/
  common.py      # Общее состояние и хелперы
  commands.py    # /start /help /files /clear /status
  admin.py       # /admin /stats /broadcast
  documents.py   # Загрузка файлов
  callbacks.py   # Callback-обработчики
  text.py        # Текстовые сообщения
```

Новый роутер = новый файл с `router = Router()`. Подключается в `main.py` через `dp.include_router()`.

## 📦 Зависимости

- Основные: `requirements.txt` (версии зафиксированы)
- Dev: `requirements-dev.txt`

Добавление новой зависимости — отдельный PR с обоснованием.

## 🐳 Docker

Перед работой с Docker:
```bash
docker compose --profile donotstart build sandbox
```

По умолчанию `DOCKER_ENABLED=true`. Для разработки можно отключить:
```bash
echo "DOCKER_ENABLED=false" >> .env
```

## ❓ Вопросы

Открывайте issue в репозитории. Если нашли уязвимость — пишите сразу в issue (это opensource-проект, security policy через issue).