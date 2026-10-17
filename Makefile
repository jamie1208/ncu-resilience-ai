# NCU Resilience AI 開發指令。在 repo 根目錄執行：make help
SHELL := /bin/bash

COMPOSE := docker compose -f infra/docker/docker-compose.yml --env-file .env
API_PORT ?= 8000
WEB_PORT ?= 8088

.DEFAULT_GOAL := help
.PHONY: help up down ps logs smoke test migrate reset

help: ## 列出可用指令
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  make %-8s %s\n", $$1, $$2}'

# .env 不存在時由範例建立（不會覆蓋既有的 .env）
.env:
	cp .env.example .env
	@echo "已由 .env.example 建立 .env；正式環境請改掉預設密碼。"

up: .env ## 建置並啟動 db、redis、api、web，等待健康檢查通過
	$(COMPOSE) up -d --build --wait

down: ## 停止並移除容器（保留資料庫資料）
	$(COMPOSE) down

ps: ## 查看服務狀態
	$(COMPOSE) ps

logs: ## 追蹤服務日誌（例如 make logs S=api）
	$(COMPOSE) logs -f $(S)

smoke: ## 冒煙測試：API health、前端首頁、前端經反向代理呼叫 API
	curl -fsS http://localhost:$(API_PORT)/api/v1/health
	@echo
	curl -fsS -o /dev/null -w "web / -> %{http_code}\n" http://localhost:$(WEB_PORT)/
	curl -fsS -o /dev/null -w "web /api/v1/health -> %{http_code}\n" http://localhost:$(WEB_PORT)/api/v1/health

test: ## 執行後端與前端測試（本機，不需要 Docker）
	cd backend && uv run pytest -q
	cd frontend && npm run test

migrate: .env ## 執行資料庫 migration（P0-T05 起可用）
	@test -f backend/alembic.ini || { echo "尚無 backend/alembic.ini：migration 於 P0-T05 建立"; exit 1; }
	$(COMPOSE) run --rm api alembic upgrade head

reset: ## 刪除所有容器與資料（資料庫、Redis）；需加 CONFIRM=yes
	@if [ "$(CONFIRM)" != "yes" ]; then \
		echo "這會永久刪除本機資料庫與 Redis 資料。確定要執行請用：make reset CONFIRM=yes"; \
		exit 1; \
	fi
	$(COMPOSE) down -v --remove-orphans
