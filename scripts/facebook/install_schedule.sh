#!/usr/bin/env bash
# Facebook NCU 社團每日擷取的 launchd 排程（P1-T13，ADR-0008）。
#
#   scripts/facebook/install_schedule.sh install    # 安裝或更新排程（每天 08:00）
#   scripts/facebook/install_schedule.sh uninstall  # 移除排程
#   scripts/facebook/install_schedule.sh status     # 查看是否已安裝、上次結束狀態
#   scripts/facebook/install_schedule.sh run-now    # 立刻執行一次（測試用）
#
# claude 執行檔：預設用 PATH 中的 claude，找不到時用 ~/.local/bin/claude；可用 CLAUDE_BIN 指定。
set -euo pipefail

LABEL="tw.ncu-resilience.fb-capture"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TEMPLATE="${REPO}/infra/launchd/${LABEL}.plist.template"
TARGET="$HOME/Library/LaunchAgents/${LABEL}.plist"
DOMAIN="gui/$(id -u)"
CLAUDE="${CLAUDE_BIN:-$(command -v claude || echo "$HOME/.local/bin/claude")}"

case "${1:-}" in
  install)
    if [[ ! -x "${CLAUDE}" ]]; then
      echo "找不到 claude 執行檔（${CLAUDE}）；請用 CLAUDE_BIN=/path/to/claude 指定" >&2
      exit 1
    fi
    mkdir -p "${REPO}/data/raw/facebook/logs" "$(dirname "${TARGET}")"
    sed -e "s|__REPO__|${REPO}|g" -e "s|__CLAUDE__|${CLAUDE}|g" "${TEMPLATE}" > "${TARGET}"
    plutil -lint "${TARGET}" >/dev/null
    launchctl bootout "${DOMAIN}/${LABEL}" 2>/dev/null || true
    launchctl bootstrap "${DOMAIN}" "${TARGET}"
    echo "已安裝：每天 08:00 執行（${TARGET}）"
    echo "日誌：${REPO}/data/raw/facebook/logs/"
    ;;
  uninstall)
    launchctl bootout "${DOMAIN}/${LABEL}" 2>/dev/null || true
    rm -f "${TARGET}"
    echo "已移除排程"
    ;;
  status)
    if launchctl print "${DOMAIN}/${LABEL}" >/dev/null 2>&1; then
      launchctl print "${DOMAIN}/${LABEL}" | grep -E "state =|last exit code|path =" || true
    else
      echo "未安裝"
    fi
    ;;
  run-now)
    launchctl kickstart "${DOMAIN}/${LABEL}"
    echo "已觸發一次；結果看 ${REPO}/data/raw/facebook/logs/capture.log"
    ;;
  *)
    echo "用法：$0 install|uninstall|status|run-now" >&2
    exit 2
    ;;
esac
