#!/usr/bin/env bash
# Facebook 每日擷取的包裝腳本（P4-T07）：launchd 經由它執行 claude。
#
#   run_capture.sh <claude 執行檔> <claude 參數...>
#
# 08:00 喚醒後網路常常還沒好（claude 回報 API Error: ENOTFOUND），所以：
# 1. 先等到 FB_CAPTURE_PROBE_URL 可連線（最多 FB_CAPTURE_NET_WAIT_SECONDS 秒）才執行。
# 2. 輸出含 "API Error" 或結束碼非 0 時，隔 FB_CAPTURE_RETRY_SECONDS 秒重試，最多 FB_CAPTURE_TRIES 次。
# 3. 輸出含「需要使用者處理」（例如 Chrome 擴充功能未連線）時不重試，等使用者處理後手動補跑。
# claude 的輸出原樣寫到 stdout（launchd 導到 capture.log）；skill 的最後回報不含貼文內容。
set -uo pipefail

if [[ $# -lt 1 ]]; then
  echo "用法：$0 <claude 執行檔> <claude 參數...>" >&2
  exit 2
fi
CLAUDE="$1"
shift

PROBE_URL="${FB_CAPTURE_PROBE_URL:-https://api.anthropic.com}"
TRIES="${FB_CAPTURE_TRIES:-3}"
RETRY_SECONDS="${FB_CAPTURE_RETRY_SECONDS:-600}"
NET_WAIT_SECONDS="${FB_CAPTURE_NET_WAIT_SECONDS:-900}"
NET_POLL_SECONDS="${FB_CAPTURE_NET_POLL_SECONDS:-30}"

log() { echo "[run_capture $(date '+%Y-%m-%d %H:%M:%S')] $*"; }

# 只要拿到任何 HTTP 回應就算可連線（狀態碼不重要）
network_ready() { curl -sS -o /dev/null --max-time 10 "${PROBE_URL}" 2>/dev/null; }

wait_for_network() {
  local waited=0
  until network_ready; do
    if (( waited >= NET_WAIT_SECONDS )); then
      return 1
    fi
    sleep "${NET_POLL_SECONDS}"
    waited=$(( waited + NET_POLL_SECONDS ))
  done
}

output="$(mktemp "${TMPDIR:-/tmp}/fb-capture.XXXXXX")"
trap 'rm -f "${output}"' EXIT

for (( attempt = 1; attempt <= TRIES; attempt++ )); do
  if ! wait_for_network; then
    log "第 ${attempt} 次：等待 ${NET_WAIT_SECONDS} 秒後網路仍無法連線"
  else
    log "第 ${attempt} 次執行"
    "${CLAUDE}" "$@" | tee "${output}"
    status=${PIPESTATUS[0]}
    if grep -q "需要使用者處理" "${output}"; then
      log "需要使用者處理，不重試"
      exit 1
    fi
    if (( status == 0 )) && ! grep -q "API Error" "${output}"; then
      exit 0
    fi
    log "第 ${attempt} 次失敗（結束碼 ${status}）"
  fi
  if (( attempt < TRIES )); then
    sleep "${RETRY_SECONDS}"
  fi
done
log "已重試 ${TRIES} 次仍失敗；請手動執行 scripts/facebook/install_schedule.sh run-now"
exit 1
