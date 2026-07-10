# ════════════════════════════════════════════════════════════════
# Makefile — профилирование
# ════════════════════════════════════════════════════════════════

.PHONY: profile-executor profile-ai profile-all profile-clean profile-ci

# Директория для .prof-файлов
PROFILE_DIR = profiles

## Запустить профилирование code_executor
profile-executor:
	ENABLE_PROFILING=true python -c "
from services.code_executor import code_executor
code_executor.execute('print(\"hello\")', 'input.xlsx', 'output.xlsx')
"
	snakeviz $(PROFILE_DIR)/execute.prof

## Запустить профилирование AI сервиса (build_prompt)
profile-ai:
	ENABLE_PROFILING=true python -c "
from services.ai_service import ai_service
result = ai_service.generate_code('test', ['file summary'], 'input.xlsx', 'output.xlsx')
"
	-snakeviz $(PROFILE_DIR)/generate_code.prof

## Запустить профилирование Gemini Assistant
profile-gemini:
	ENABLE_PROFILING=true python -c "
from services.gemini_assistant import gemini_assistant
import asyncio
asyncio.run(gemini_assistant.execute('code', 'input.xlsx', 'output.xlsx', 'test'))
"
	-snakeviz $(PROFILE_DIR)/execute.prof

## Запустить yappi профилирование бота (30 секунд)
profile-bot:
	ENABLE_PROFILING=true python -m yappi main.py &
	sleep 30
	kill %1 2>/dev/null; true
	-snakeviz $(PROFILE_DIR)/async_profile.pstat

## Открыть последний .prof файл
profile-view:
	@latest=$$(ls -t $(PROFILE_DIR)/*.prof 2>/dev/null | head -1); \
	if [ -n "$$latest" ]; then \
		echo "📊 Открываю: $$latest"; \
		snakeviz "$$latest"; \
	else \
		echo "❌ Нет .prof файлов в $(PROFILE_DIR)/"; \
	fi

## Удалить все профили
profile-clean:
	rm -rf $(PROFILE_DIR)/*.prof $(PROFILE_DIR)/*.pstat
	@echo "🧹 Профили удалены"

## ============================================================
## Всё сразу: код + AI
## ============================================================
profile-all: profile-clean profile-executor profile-ai
	@echo "✅ Профилирование завершено"
	@echo "Смотри: snakeviz $(PROFILE_DIR)/*.prof"

## ============================================================
## CI-режим (без snakeviz)
## ============================================================
profile-ci:
	ENABLE_PROFILING=true python -c "
from services.code_executor import code_executor
code_executor.execute('print(\"hello\")', 'input.xlsx', 'output.xlsx')
from services.ai_service import ai_service
result = ai_service.generate_code('test', ['file info'], 'input.xlsx', 'output.xlsx')
"
	@echo "✅ Профили сохранены в $(PROFILE_DIR)/"
	@ls -la $(PROFILE_DIR)/
