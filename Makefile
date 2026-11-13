# NCU Resilience AI 開發指令。在 repo 根目錄執行：make help
SHELL := /bin/bash

COMPOSE := docker compose -f infra/docker/docker-compose.yml --env-file .env
# 本機常駐運行（P5-T06）：疊上 docker-compose.local.yml
COMPOSE_LOCAL := docker compose -f infra/docker/docker-compose.yml -f infra/docker/docker-compose.local.yml --env-file .env
# 示範環境（P9-T01，ADR-0022）：獨立的 compose project ncu-demo，與真實環境不共用任何容器或資料
COMPOSE_DEMO := docker compose -p ncu-demo -f infra/docker/docker-compose.yml -f infra/docker/docker-compose.demo.yml -f infra/docker/docker-compose.demo-share.yml --env-file .env
DEMO_SERVICES := db redis api worker beat web
API_PORT ?= 8000
WEB_PORT ?= 8088

.DEFAULT_GOAL := help
.PHONY: help up up-local listen down ps logs smoke test e2e migrate sources health reset demo-up demo-seed demo-down demo-drop demo-share demo-share-url demo-unshare

help: ## 列出可用指令
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  make %-15s %s\n", $$1, $$2}'

# .env 不存在時由範例建立（不會覆蓋既有的 .env）
.env:
	cp .env.example .env
	@echo "已由 .env.example 建立 .env；正式環境請改掉預設密碼。"

up: .env ## 建置並啟動 db、redis、api、web、worker、beat，等待健康檢查通過
	$(COMPOSE) up -d --build --wait

up-local: .env ## 本機常駐運行（P5-T06）：自動重啟、日誌輪替、埠號只綁 127.0.0.1；見 docs/runbooks/local-operation.md
	$(COMPOSE_LOCAL) up -d --build --wait

listen: ## 列出 Docker 在主機上監聽的 TCP 埠（應全部是 127.0.0.1）
	@lsof -nP -iTCP -sTCP:LISTEN | awk 'NR == 1 || /com\.docke|vpnkit/'

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

e2e: .env ## 附錄 A 端到端測試（P3-T10）：啟動 db，在獨立的 e2e 資料庫建立案例後以真實 API＋前端跑 Playwright
	$(COMPOSE) up -d --wait db redis
	cd frontend && NODE_PATH=$$PWD/node_modules npx playwright test -c ../tests/e2e/playwright.config.ts

migrate: .env ## 執行資料庫 migration（P0-T05 起可用）
	@test -f backend/alembic.ini || { echo "尚無 backend/alembic.ini：migration 於 P0-T05 建立"; exit 1; }
	$(COMPOSE) run --rm api alembic upgrade head

sources: .env ## 依 config/sources.yaml 建立缺少的來源資料列（P1-T07；不改既有來源）
	$(COMPOSE) run --rm api python -m app.ingestion.sync_sources

health: .env ## 顯示各來源擷取健康狀態（P1-T08；7 天成功率、連續失敗、死信）
	$(COMPOSE) run --rm api python -m app.ingestion.health

reset: ## 刪除所有容器與資料（資料庫、Redis）；需加 CONFIRM=yes
	@if [ "$(CONFIRM)" != "yes" ]; then \
		echo "這會永久刪除本機資料庫與 Redis 資料。確定要執行請用：make reset CONFIRM=yes"; \
		exit 1; \
	fi
	$(COMPOSE) down -v --remove-orphans

# ---------- 示範環境（P9-T01，ADR-0022）：見 docs/runbooks/demo.md ----------

demo-up: .env ## 啟動示範環境（虛構資料，http://127.0.0.1:8089）並重新播種；不影響真實環境
	$(COMPOSE_DEMO) up -d --build --wait $(DEMO_SERVICES)
	$(MAKE) demo-seed

demo-seed: .env ## 清空示範資料庫並重新建立虛構資料（只在 ncu_resilience_demo 執行）
	@# 播種期間先停 worker、beat，避免排程的公開同步與種子同時寫入
	$(COMPOSE_DEMO) stop worker beat
	$(COMPOSE_DEMO) exec api alembic upgrade head
	$(COMPOSE_DEMO) exec api python -m app.demo.seed
	$(COMPOSE_DEMO) start worker beat

demo-down: ## 停止示範環境（含分享通道；保留示範資料）
	$(COMPOSE_DEMO) --profile share down

demo-drop: ## 刪除示範環境的容器與 volume（只刪 ncu-demo，真實資料不受影響）；需加 CONFIRM=yes
	@if [ "$(CONFIRM)" != "yes" ]; then \
		echo "這會刪除示範環境的資料庫與 Redis（ncu-demo_*）。確定要執行請用：make demo-drop CONFIRM=yes"; \
		exit 1; \
	fi
	$(COMPOSE_DEMO) --profile share down -v --remove-orphans --rmi local

# 示範站分享（ADR-0022 決策 9）：只開關示範站的通道；真實網站用 infra/tunnel/tunnel.sh up／down
DEMO_SHARE_URL = $(COMPOSE_DEMO) --profile share logs --no-log-prefix tunnel 2>/dev/null | grep -Eo 'https://[a-z0-9-]+\.trycloudflare\.com' | tail -n 1
DEMO_SHARE_RUNNING = $(COMPOSE_DEMO) --profile share ps -q --status running tunnel 2>/dev/null

demo-share: .env ## 開放示範站給組員：建立臨時網址（公開站與 /admin），印出網址；真實網站不受影響
	$(COMPOSE_DEMO) up -d --wait $(DEMO_SERVICES)
	@# 每次重建通道容器：Quick Tunnel 每次啟動網址都會變，重建才不會讀到舊網址
	$(COMPOSE_DEMO) --profile share up -d --force-recreate --no-deps tunnel
	@for i in $$(seq 1 40); do \
		url=$$($(DEMO_SHARE_URL)); \
		if [ -n "$$url" ]; then \
			echo "示範站已開放：$$url　後台：$$url/admin"; \
			echo "關閉：make demo-unshare"; exit 0; \
		fi; \
		sleep 1; \
	done; \
	echo "通道已啟動但還沒取得網址，稍後執行 make demo-share-url"

demo-share-url: ## 印出示範站目前的分享網址（沒有開放時說明未開放）
	@if [ -n "$$($(DEMO_SHARE_RUNNING))" ]; then $(DEMO_SHARE_URL); \
	else echo "示範站目前沒有開放分享"; fi

demo-unshare: ## 關閉示範站分享：停止通道，網址立即失效；示範站本機照常運作
	$(COMPOSE_DEMO) --profile share stop tunnel
	@echo "示範站分享已關閉（真實網站的通道不受影響）"
