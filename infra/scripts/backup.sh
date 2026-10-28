#!/usr/bin/env bash
# PostgreSQL 備份（P5-T05）。用法：bash infra/scripts/backup.sh
#
# 以 pg_dump 自訂格式（-Fc）備份本機 docker compose 的資料庫，產生三個檔案：
#   <名稱>.dump         pg_dump 自訂格式
#   <名稱>.counts.tsv   與 dump 同一個快照的各表筆數（restore.sh 以此驗證）
#   <名稱>.sha256       dump 與筆數檔的 SHA-256
# 備份含個資與投稿內容，預設放在 repo 外（NCU_BACKUP_DIR，預設 ~/ncu-resilience-backups），
# 目錄權限 700、檔案權限 600。不刪除任何舊備份。
# 連線帳密只在 db 容器內以環境變數取得，本腳本不讀也不印出密碼。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BACKUP_DIR="${NCU_BACKUP_DIR:-$HOME/ncu-resilience-backups}"
COMPOSE=(docker compose -f "$ROOT/infra/docker/docker-compose.yml" --env-file "$ROOT/.env")

usage() {
  sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'
}

case "${1:-}" in
  "") ;;
  -h | --help) usage; exit 0 ;;
  *) echo "未知參數：${1}" >&2; usage >&2; exit 2 ;;
esac

[ -f "$ROOT/.env" ] || { echo "找不到 $ROOT/.env；請先 make up" >&2; exit 1; }
"${COMPOSE[@]}" exec -T db pg_isready -q || { echo "db 容器未就緒；請先 make up" >&2; exit 1; }

umask 077
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

NAME="ncu-resilience-$(date -u +%Y%m%dT%H%M%SZ)"
DUMP="$BACKUP_DIR/$NAME.dump"
COUNTS="$BACKUP_DIR/$NAME.counts.tsv"
WORK="$(mktemp -d)"
PSQL_PID=""

cleanup() {
  exec 3>&- 4<&- 2>/dev/null || true
  [ -n "$PSQL_PID" ] && wait "$PSQL_PID" 2>/dev/null || true
  rm -rf "$WORK"
}
fail() {
  echo "備份失敗：${1}" >&2
  rm -f "$DUMP" "$COUNTS" "$BACKUP_DIR/$NAME.sha256"
  exit 1
}
trap cleanup EXIT

# 以一個保持開啟的 psql 工作階段匯出快照，pg_dump 與筆數統計都用同一個快照，
# 備份期間 worker 繼續寫入也不會讓筆數與 dump 不一致。（macOS 內建 bash 3.2 沒有 coproc，改用 FIFO。）
mkfifo "$WORK/in" "$WORK/out"
"${COMPOSE[@]}" exec -T db sh -c 'psql -qAtX -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
  <"$WORK/in" >"$WORK/out" &
PSQL_PID=$!
exec 3>"$WORK/in" 4<"$WORK/out"

echo "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY; SELECT pg_export_snapshot();" >&3
read -r -t 30 SNAPSHOT <&4 || fail "無法匯出快照"
[[ "$SNAPSHOT" =~ ^[0-9A-F-]+$ ]] || fail "快照識別碼格式不正確：${SNAPSHOT}"

# public schema 每張表的 exact count，最後一行 END 作為結束標記
cat >&3 <<'SQL'
SELECT format('SELECT %L || E''\t'' || count(*) FROM %I.%I;', tablename, schemaname, tablename)
  FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename
\gexec
SELECT 'END';
SQL
: >"$COUNTS"
while read -r -t 120 line <&4; do
  [ "$line" = "END" ] && break
  printf '%s\n' "$line" >>"$COUNTS"
done
[ "$line" = "END" ] || fail "各表筆數統計未完成"
[ -s "$COUNTS" ] || fail "public schema 沒有任何資料表"

"${COMPOSE[@]}" exec -T db sh -c \
  "pg_dump -Fc --snapshot='$SNAPSHOT' -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\"" >"$DUMP" \
  || fail "pg_dump 失敗"

echo "COMMIT;" >&3
exec 3>&-
wait "$PSQL_PID" || fail "psql 工作階段異常結束"
PSQL_PID=""

# dump 至少要能被 pg_restore 讀出目錄
"${COMPOSE[@]}" exec -T db pg_restore --list <"$DUMP" >/dev/null || fail "dump 無法被 pg_restore 讀取"

(cd "$BACKUP_DIR" && shasum -a 256 "$NAME.dump" "$NAME.counts.tsv" >"$NAME.sha256")
chmod 600 "$DUMP" "$COUNTS" "$BACKUP_DIR/$NAME.sha256"

TABLES=$(wc -l <"$COUNTS" | tr -d ' ')
ROWS=$(awk -F'\t' '{s += $2} END {print s}' "$COUNTS")
SIZE=$(du -h "$DUMP" | cut -f1 | tr -d ' ')
echo "備份完成：${DUMP}（${SIZE}，${TABLES} 張表，共 ${ROWS} 筆）"
