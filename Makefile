# Makefile для управления стеком RMM-RA (MeshCentral + RustDesk)
# Принцип KISS: всё управление из одного места

# Переменные
COMPOSE_FILE = docker-compose.yml
ENV_FILE     = .env
BACKUP_DIR   = backups
TZ           ?= Europe/Moscow

# Цвета для вывода в консоль
GREEN  = \033[0;32m
YELLOW = \033[1;33m
NC     = \033[0m # No Color

.PHONY: help up down restart logs logs-mc logs-rd status pull update clean backup lock-registration rustdesk-key

help: ## Показать эту справку
	@echo "$(YELLOW)Доступные команды для управления RMM-RA:$(NC)"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  $(GREEN)%-18s$(NC) %s\n", $$1, $$2}'

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
		-e TZ=$(TZ) \
		-v "$(CURDIR):/src:ro" \
		-v "$(CURDIR)/$(BACKUP_DIR):/backup" \
		alpine:latest sh -c 'tar czf /backup/rmm-ra-$$(date +%Y%m%d-%H%M%S).tar.gz -C /src docker-compose.yml .env .env.example Makefile README.md .gitignore scripts meshcentral-data rustdesk-data && chmod 600 /backup/*.tar.gz'
	@echo "$(GREEN)Готово. Архив лежит в $(BACKUP_DIR)/$(NC)"

lock-registration: ## Закрыть регистрацию новых аккаунтов (после создания админа)
	./scripts/lock-registration.sh

# Полезная команда для RustDesk: показать сгенерированный ключ
rustdesk-key: ## Показать публичный ключ RustDesk (нужен для настройки клиентов)
	@echo "$(YELLOW)Публичный ключ RustDesk (id_ed25519.pub):$(NC)"
	@cat ./rustdesk-data/id_ed25519.pub 2>/dev/null || echo "Ключ еще не сгенерирован. Запустите 'make up'."
