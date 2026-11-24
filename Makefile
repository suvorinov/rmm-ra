# Makefile для управления стеком RMM-RA (MeshCentral + RustDesk)
# Принцип KISS: всё управление из одного места

# Переменные
COMPOSE_FILE = docker-compose.yml
MONITOR_COMPOSE_FILE = docker-compose.monitor.yml
MONITOR_DATA_DIR = monitor-data
ENV_FILE     = .env
BACKUP_DIR   = backups
TZ           ?= Europe/Moscow

# Цвета для вывода в консоль
GREEN  = \033[0;32m
YELLOW = \033[1;33m
NC     = \033[0m # No Color

.PHONY: help up down restart logs logs-mc logs-rd status pull update clean backup lock-registration mesh-groups mesh-doctor pin-cert npm-ws-timeout reset-2fa rustdesk-info rustdesk-book rustdesk-key monitor-up monitor-down monitor-restart monitor-build monitor-logs monitor-status monitor-health monitor-backup

help: ## Показать эту справку
	@echo "$(YELLOW)Доступные команды для управления RMM-RA:$(NC)"
	@grep -E '^[a-zA-Z0-9_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  $(GREEN)%-18s$(NC) %s\n", $$1, $$2}'

up: ## Запустить все сервисы в фоновом режиме
	@echo "$(YELLOW)Запуск сервисов MeshCentral и RustDesk...$(NC)"
	docker compose -f $(COMPOSE_FILE) up -d
	@echo "$(GREEN)Готово! Сервисы запущены.$(NC)"

down: ## Остановить все сервисы (без удаления данных)
	@echo "$(YELLOW)Остановка сервисов...$(NC)"
	docker compose -f $(COMPOSE_FILE) down
	@echo "$(GREEN)Сервисы остановлены.$(NC)"

restart: ## Перезапустить все сервисы
	@echo "$(YELLOW)Перезапуск сервисов...$(NC)"
	docker compose -f $(COMPOSE_FILE) down
	docker compose -f $(COMPOSE_FILE) up -d
	@echo "$(GREEN)Сервисы перезапущены.$(NC)"

logs: ## Просмотр логов всех сервисов (Ctrl+C для выхода)
	docker compose -f $(COMPOSE_FILE) logs -f

logs-mc: ## Просмотр логов только MeshCentral
	docker compose -f $(COMPOSE_FILE) logs -f meshcentral

logs-rd: ## Просмотр логов только RustDesk
	docker compose -f $(COMPOSE_FILE) logs -f hbbs hbbr

status: ## Показать текущий статус контейнеров
	docker compose -f $(COMPOSE_FILE) ps

pull: ## Скачать свежие версии образов
	docker compose -f $(COMPOSE_FILE) pull

update: ## Обновить образы и перезапустить стек
	docker compose -f $(COMPOSE_FILE) pull
	docker compose -f $(COMPOSE_FILE) up -d

clean: ## Полная очистка (ОСТОРОЖНО: удаляет контейнеры и сети, но НЕ данные)
	@echo "$(YELLOW)Полная очистка стека...$(NC)"
	docker compose -f $(COMPOSE_FILE) down --remove-orphans
	@echo "$(GREEN)Очистка завершена. Данные в папках *-data сохранены.$(NC)"

backup: ## Создать архив данных и конфигов в ./backups (права 600)
	@mkdir -p $(BACKUP_DIR)
	@echo "$(YELLOW)Создаю бэкап...$(NC)"
	docker run --rm \
		-e BACKUP_NAME="rmm-ra-$$(date +%Y%m%d-%H%M%S).tar.gz" \
		-v "$(CURDIR):/src:ro" \
		-v "$(CURDIR)/$(BACKUP_DIR):/backup" \
		alpine:latest sh -c 'tar czf "/backup/$$BACKUP_NAME" -C /src docker-compose.yml docker-compose.monitor.yml .env .env.example Makefile README.md MONITORING.md .gitignore scripts meshcentral-data rustdesk-data && chmod 600 /backup/*.tar.gz'
	@echo "$(GREEN)Готово. Архив лежит в $(BACKUP_DIR)/$(NC)"
	@echo "$(YELLOW)БД мониторинга в архив не входит: make monitor-backup$(NC)"

lock-registration: ## Закрыть регистрацию новых аккаунтов (после создания админа)
	./scripts/lock-registration.sh

mesh-groups: ## Показать ID групп устройств MeshCentral (для установки агентов)
	@./scripts/mesh-id.sh; rc=$$?; if [ $$rc -eq 2 ]; then exit 0; else exit $$rc; fi

pin-cert: ## Пришпилить сертификат NPM в MeshCentral (после смены сертификата)
	./scripts/pin-npm-cert.sh

npm-ws-timeout: ## Увеличить таймауты WebSocket в NPM (иначе агенты рвутся каждые 90с)
	./scripts/npm-ws-timeout.sh

mesh-doctor: ## Проверить сертификат, таймауты NPM и состояние устройств
	@./scripts/mesh-doctor.sh

reset-2fa: ## Аварийный сброс 2FA у админа (потерян телефон и резервные коды)
	@echo "$(YELLOW)Только показать состояние:$(NC) ./scripts/reset-2fa.sh"
	@echo "$(YELLOW)Сбросить (осторожно!):$(NC)   ./scripts/reset-2fa.sh --apply --yes"

rustdesk-info: ## Параметры RustDesk для клиентов (адрес, ключ, порты, пиры)
	@./scripts/rustdesk-info.sh

rustdesk-book: ## HTML-адресная книга из отчётов Ansible (reports/rustdesk-book.html)
	@./scripts/rustdesk-book.sh

# Полезная команда для RustDesk: показать сгенерированный ключ
rustdesk-key: ## Показать публичный ключ RustDesk (нужен для настройки клиентов)
	@echo "$(YELLOW)Публичный ключ RustDesk (id_ed25519.pub):$(NC)"
	@cat ./rustdesk-data/id_ed25519.pub 2>/dev/null || echo "Ключ еще не сгенерирован. Запустите 'make up'."

# ============================================================
#  Мониторинг (FastAPI + SQLite поверх MeshCentral)
# ============================================================

monitor-up: ## Запустить сервис мониторинга (нужен monitor/monitor.env)
	@test -f monitor/monitor.env || { \
		echo "$(YELLOW)Нет monitor/monitor.env.$(NC)"; \
		echo "  cp monitor/monitor.env.example monitor/monitor.env"; \
		echo "  chmod 600 monitor/monitor.env"; \
		echo "  затем впиши пароль сервисного аккаунта MeshCentral"; \
		exit 1; }
	@install -d -m 700 $(MONITOR_DATA_DIR)
	docker compose -f $(MONITOR_COMPOSE_FILE) up -d --build
	@echo "$(GREEN)Мониторинг: http://127.0.0.1:8000 (через NPM — mon.zr.local)$(NC)"

monitor-down: ## Остановить сервис мониторинга (данные сохраняются)
	docker compose -f $(MONITOR_COMPOSE_FILE) down

monitor-restart: ## Перезапустить сервис мониторинга
	docker compose -f $(MONITOR_COMPOSE_FILE) restart monitor

monitor-build: ## Пересобрать образ мониторинга
	docker compose -f $(MONITOR_COMPOSE_FILE) build --pull

monitor-logs: ## Логи мониторинга (Ctrl+C для выхода)
	docker compose -f $(MONITOR_COMPOSE_FILE) logs -f

monitor-status: ## Статус контейнера мониторинга и healthz
	@docker compose -f $(MONITOR_COMPOSE_FILE) ps
	@echo
	@docker exec rmm-monitor python -c "import json,urllib.request; print(json.dumps(json.load(urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=5)), ensure_ascii=False, indent=2))" 2>/dev/null || true

monitor-health: ## Проверить healthz и последние ошибки синхронизации
	@docker exec rmm-monitor python -c "import json,urllib.request; print(json.dumps(json.load(urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=5)), ensure_ascii=False, indent=2))"

monitor-backup: ## Сделать согласованный бэкап SQLite в ./backups (права 600)
	@mkdir -p $(BACKUP_DIR)
	@STAMP=$$(date +%Y%m%d-%H%M%S); \
	docker exec rmm-monitor python -c "import sqlite3; s=sqlite3.connect('/data/monitor.db'); d=sqlite3.connect('/data/.backup.db'); s.backup(d); d.close(); s.close()"; \
	docker cp rmm-monitor:/data/.backup.db "$(BACKUP_DIR)/monitor-$$STAMP.db"; \
	docker exec rmm-monitor rm -f /data/.backup.db; \
	chmod 600 "$(BACKUP_DIR)/monitor-$$STAMP.db"; \
	echo "$(GREEN)Бэкап: $(BACKUP_DIR)/monitor-$$STAMP.db$(NC)"
