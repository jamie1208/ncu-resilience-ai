#!/usr/bin/env bash
# 通道對外操作（P5-T15，ADR-0015）。步驟說明見 docs/runbooks/deploy.md。
#
#   bash infra/tunnel/tunnel.sh gateway        啟動公開閘道（只綁 127.0.0.1:8090，不對外）
#   bash infra/tunnel/tunnel.sh check [網址]   檢查只有公開路徑可存取（預設 http://127.0.0.1:8090）
#   bash infra/tunnel/tunnel.sh up             啟動 Quick Tunnel（對外上線），印出網址
#   bash infra/tunnel/tunnel.sh url            印出目前的通道網址
#   bash infra/tunnel/tunnel.sh down           停止並移除通道（暫停／緊急下線），閘道與管理端照常運作
#   bash infra/tunnel/tunnel.sh status         顯示閘道與通道狀態
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COMPOSE=(docker compose
  -f "$REPO/infra/docker/docker-compose.yml"
  -f "$REPO/infra/docker/docker-compose.local.yml"
  -f "$REPO/infra/tunnel/docker-compose.tunnel.yml"
  --env-file "$REPO/.env")
GATEWAY_URL="http://127.0.0.1:${PUBLIC_GATEWAY_PORT:-8090}"

tunnel_url() {
  "${COMPOSE[@]}" --profile tunnel logs --no-log-prefix tunnel 2>/dev/null \
    | grep -Eo 'https://[a-z0-9-]+\.trycloudflare\.com' | tail -n 1 || true
}

# 期望的狀態碼：allow＝2xx；deny＝404；method＝不可為 2xx（不允許的方法）；
# reach＝請求有送到後端（後端的驗證錯誤 4xx 可以，閘道的 403／404 不行）
FAILED=0
expect() {
  local kind="$1" method="$2" path="$3" code
  code="$(curl -s -o /dev/null -w '%{http_code}' --path-as-is --max-time 15 -X "$method" \
    -H 'Content-Type: application/json' ${4:+--data "$4"} "$BASE$path" || echo 000)"
  local ok=0
  case "$kind" in
    allow) [[ "$code" == 2* ]] && ok=1 ;;
    deny) [[ "$code" == 404 ]] && ok=1 ;;
    method) [[ "$code" != 2* && "$code" != 000 ]] && ok=1 ;;
    reach) [[ "$code" == 4* && "$code" != 403 && "$code" != 404 ]] && ok=1 ;;
  esac
  if ((ok)); then
    printf '  ok    %-6s %-6s %-45s %s\n' "$kind" "$method" "$path" "$code"
  else
    printf '  FAIL  %-6s %-6s %-45s %s\n' "$kind" "$method" "$path" "$code"
    FAILED=1
  fi
}

check() {
  BASE="${1:-$GATEWAY_URL}"
  BASE="${BASE%/}"
  echo "檢查 $BASE"
  local asset
  asset="$(curl -s --max-time 15 "$BASE/" | grep -Eo '/assets/[^"]+\.js' | head -n 1 || true)"
  if [[ -z "$asset" ]]; then
    echo "  FAIL  首頁沒有回傳 index.html（閘道或 web 未啟動？）"
    exit 1
  fi

  echo "公開網站與公開 API（應可存取）："
  for path in / /events /events/00000000-0000-0000-0000-000000000000 /progress /resilience /about /report "$asset" \
    /api/v1/public/feed /api/v1/public/notices /api/v1/public/categories \
    /api/v1/public/events /api/v1/public/progress /api/v1/public/resilience; do
    expect allow GET "$path"
  done
  # 匿名回報：送空內容，只確認 POST 有到後端（得到驗證錯誤），不建立任何投稿
  expect reach POST /api/v1/public/reports '{}'

  echo "管理端、登入與其他內部路徑（應一律 404）："
  for path in /admin /admin/ /admin/events /admin/publications /index.html \
    /api/v1/admin/events /api/v1/admin/publications /api/v1/auth/me /api/v1/auth/login \
    /api/v1/health /api/v1/ /api/ /docs /redoc /openapi.json /metrics /assets \
    /api/v1/public/../admin/events /api/v1/public/%2e%2e/admin/events \
    /api/v1/public/..%2fadmin/events //admin /%2fadmin /api/v1/public%2f../admin/events; do
    expect deny GET "$path"
  done
  expect deny POST /api/v1/auth/login '{"username":"x","password":"x"}'
  expect deny POST /api/v1/auth/logout
  expect deny POST /api/v1/admin/publications/x/takedown

  echo "公開路徑上不允許的方法（不可成功）："
  expect method DELETE /api/v1/public/events
  expect method PUT /api/v1/public/reports '{}'
  expect method POST /events

  if ((FAILED)); then
    echo "結果：有路徑不符預期，不要上線"
    exit 1
  fi
  echo "結果：只有公開路徑可存取"
}

case "${1:-}" in
  gateway)
    "${COMPOSE[@]}" up -d --wait public-gateway
    echo "公開閘道：${GATEWAY_URL}（只綁 127.0.0.1）"
    ;;
  check)
    check "${2:-}"
    ;;
  up)
    "${COMPOSE[@]}" up -d --wait public-gateway
    check "$GATEWAY_URL"
    "${COMPOSE[@]}" --profile tunnel up -d tunnel
    url=""
    for _ in $(seq 1 30); do
      url="$(tunnel_url)"
      [[ -n "$url" ]] && break
      sleep 2
    done
    if [[ -z "$url" ]]; then
      echo "60 秒內沒有取得通道網址，查看：docker compose ... --profile tunnel logs tunnel" >&2
      exit 1
    fi
    echo "已上線：$url"
    echo "請從外部網路執行：bash infra/tunnel/tunnel.sh check $url"
    ;;
  url)
    url="$(tunnel_url)"
    [[ -n "$url" ]] || { echo "通道未啟動" >&2; exit 1; }
    echo "$url"
    ;;
  down)
    "${COMPOSE[@]}" --profile tunnel rm -sf tunnel
    echo "通道已停止，公開網站不再對外；管理端與閘道照常在本機運作"
    ;;
  status)
    "${COMPOSE[@]}" --profile tunnel ps public-gateway tunnel
    ;;
  *)
    sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'
    exit 2
    ;;
esac
