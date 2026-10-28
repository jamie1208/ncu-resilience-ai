#!/usr/bin/env bash
# PostgreSQL 還原（P5-T05）。用法：
#   bash infra/scripts/restore.sh --target local [--file <dump>] [--database <名稱>] [--replace] [--keep]
#
#   --target local   還原到本機 docker compose 的 db 容器（目前唯一支援的目標）
#   --file           要還原的 .dump；預設為 NCU_BACKUP_DIR 中最新的一份
#   --database       還原到哪個資料庫；預設為演練用的 <POSTGRES_DB>_restore_drill
#   --replace        目標資料庫已存在時，先刪除再還原（演練用資料庫不需要此參數）
#   --keep           演練用資料庫驗證後保留（預設驗證完即刪除）
#
# 還原前核對 SHA-256，還原後逐表比對 backup.sh 記錄的筆數，不一致就以非 0 結束。
# 還原到正式資料庫（--database 等於 POSTGRES_DB）會先停止 api、worker、beat，
# 需要 --replace 並輸入資料庫名稱確認；步驟見 docs/runbooks/restore.md。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BACKUP_DIR="${NCU_BACKUP_DIR:-$HOME/ncu-resilience-backups}"
COMPOSE=(docker compose -f "$ROOT/infra/docker/docker-compose.yml" --env-file "$ROOT/.env")
APP_SERVICES=(api worker beat)

usage() {
  sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'
}
die() {
  echo "還原失敗：${1}" >&2
  exit 1
}

TARGET="" FILE="" DATABASE="" REPLACE=0 KEEP=0
while [ $# -gt 0 ]; do
  case "$1" in
    --target) TARGET="${2:-}"; shift 2 ;;
    --file) FILE="${2:-}"; shift 2 ;;
    --database) DATABASE="${2:-}"; shift 2 ;;
    --replace) REPLACE=1; shift ;;
    --keep) KEEP=1; shift ;;
    -h | --help) usage; exit 0 ;;
    *) echo "未知參數：${1}" >&2; usage >&2; exit 2 ;;
  esac
done
[ "$TARGET" = "local" ] || { echo "必須指定 --target local" >&2; usage >&2; exit 2; }

[ -f "$ROOT/.env" ] || die "找不到 $ROOT/.env；請先 make up"
"${COMPOSE[@]}" exec -T db pg_isready -q || die "db 容器未就緒；請先 make up"

# 容器內執行 psql（-U／-d 由容器環境變數決定，密碼不經過本腳本）
db_sh() {
  "${COMPOSE[@]}" exec -T db sh -c "$1"
}
psql_on() { # $1 = 資料庫名稱，SQL 從 stdin 讀取
  db_sh "psql -qAtX -v ON_ERROR_STOP=1 -U \"\$POSTGRES_USER\" -d '$1'"
}

MAIN_DB=$(db_sh 'printf %s "$POSTGRES_DB"')
DRILL_DB="${MAIN_DB}_restore_drill"
DATABASE="${DATABASE:-$DRILL_DB}"
[[ "$DATABASE" =~ ^[a-z_][a-z0-9_]*$ ]] || die "資料庫名稱只能使用小寫英數與底線：${DATABASE}"
case "$DATABASE" in postgres | template0 | template1) die "不能還原到系統資料庫 ${DATABASE}" ;; esac

if [ -z "$FILE" ]; then
  FILE=$(ls -1 "$BACKUP_DIR"/ncu-resilience-*.dump 2>/dev/null | sort | tail -n 1 || true)
  [ -n "$FILE" ] || die "$BACKUP_DIR 中沒有備份；請先執行 infra/scripts/backup.sh"
fi
[ -f "$FILE" ] || die "找不到備份檔 ${FILE}"
DIR="$(cd "$(dirname "$FILE")" && pwd)"
BASE="$(basename "$FILE" .dump)"
COUNTS="$DIR/$BASE.counts.tsv"
SUMS="$DIR/$BASE.sha256"
[ -f "$COUNTS" ] && [ -f "$SUMS" ] || die "缺少 $BASE.counts.tsv 或 $BASE.sha256，無法驗證這份備份"

(cd "$DIR" && shasum -a 256 -c --quiet "$BASE.sha256") || die "SHA-256 不符，備份檔可能損毀或被修改"
echo "SHA-256 核對通過：$BASE"

EXISTS=$(echo "SELECT 1 FROM pg_database WHERE datname = '$DATABASE';" | psql_on postgres)
if [ "$DATABASE" = "$MAIN_DB" ]; then
  [ "$REPLACE" = 1 ] || die "還原到正式資料庫 ${MAIN_DB} 需要 --replace"
  [ -t 0 ] || die "還原到正式資料庫需要在終端機互動確認"
  printf '這會刪除並重建正式資料庫 %s，目前資料以備份 %s 取代。\n請輸入資料庫名稱確認：' "$MAIN_DB" "$BASE"
  read -r answer
  [ "$answer" = "$MAIN_DB" ] || die "確認不符，未做任何變更"
  "${COMPOSE[@]}" stop "${APP_SERVICES[@]}"
  RESTART_APP=1
elif [ -n "$EXISTS" ] && [ "$DATABASE" != "$DRILL_DB" ] && [ "$REPLACE" != 1 ]; then
  die "資料庫 $DATABASE 已存在；要覆蓋請加 --replace"
fi
RESTART_APP="${RESTART_APP:-0}"
restart_app() {
  if [ "$RESTART_APP" = 1 ]; then
    echo "重新啟動 ${APP_SERVICES[*]}"
    "${COMPOSE[@]}" start "${APP_SERVICES[@]}"
  fi
}
trap restart_app EXIT

db_sh "dropdb --if-exists --force -U \"\$POSTGRES_USER\" '$DATABASE' && createdb -U \"\$POSTGRES_USER\" '$DATABASE'"
"${COMPOSE[@]}" exec -T db sh -c \
  "pg_restore --no-owner --exit-on-error --single-transaction -U \"\$POSTGRES_USER\" -d '$DATABASE'" <"$FILE" \
  || die "pg_restore 失敗（$DATABASE 可能不完整）"
echo "已還原到資料庫 $DATABASE"

# 逐表比對筆數：表的集合與每張表的筆數都必須和備份當下相同
ACTUAL="$(mktemp)"
trap 'rm -f "$ACTUAL"; restart_app' EXIT
psql_on "$DATABASE" >"$ACTUAL" <<'SQL'
SELECT format('SELECT %L || E''\t'' || count(*) FROM %I.%I;', tablename, schemaname, tablename)
  FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename
\gexec
SQL
if ! diff -u --label "備份當下" --label "還原後" "$COUNTS" "$ACTUAL"; then
  die "還原後的資料表或筆數與備份不一致（$DATABASE 已保留供檢查）"
fi
TABLES=$(wc -l <"$ACTUAL" | tr -d ' ')
ROWS=$(awk -F'\t' '{s += $2} END {print s}' "$ACTUAL")
VERSION=$(echo "SELECT version_num FROM alembic_version;" | psql_on "$DATABASE")
echo "驗證通過：${TABLES} 張表、共 ${ROWS} 筆與備份一致；alembic 版本 ${VERSION}"

if [ "$DATABASE" = "$DRILL_DB" ] && [ "$KEEP" != 1 ]; then
  db_sh "dropdb --force -U \"\$POSTGRES_USER\" '$DRILL_DB'"
  echo "已刪除演練用資料庫 ${DRILL_DB}（要保留請加 --keep）"
fi
