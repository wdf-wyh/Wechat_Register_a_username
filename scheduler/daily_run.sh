#!/usr/bin/env bash
# Linux 每日养号入口（crontab 调用）。按 registration_date 自动选当天剧本。
#
# crontab -e 示例（机器本地时区请先 timedatectl）:
#   0 7 * * *  /path/to/Wechat_farm/scheduler/daily_run.sh run
#   0 23 * * * /path/to/Wechat_farm/scheduler/daily_run.sh report
#   0 8 * * 1  /path/to/Wechat_farm/scheduler/daily_run.sh advance
#
# 不要调用 cold-start-burst。

set -euo pipefail

CMD="${1:-run}"
case "$CMD" in
  run|report|advance) ;;
  *) echo "用法: $0 [run|report|advance]" >&2; exit 2 ;;
esac

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

LOG_DIR="$ROOT/logs"
mkdir -p "$LOG_DIR"
CRON_LOG="$LOG_DIR/cron.log"
LOCK_FILE="$LOG_DIR/daily_${CMD}.lock"
PY="$ROOT/wechat_env/bin/python"
if [[ ! -x "$PY" ]]; then
  PY="$ROOT/wechat_env/Scripts/python.exe"
fi

log() {
  echo "$(date '+%Y-%m-%d %H:%M:%S') | $*" | tee -a "$CRON_LOG"
}

if [[ ! -x "$PY" && ! -f "$PY" ]]; then
  log "[FAIL] 找不到虚拟环境 python: $PY"
  exit 1
fi

if [[ -f "$LOCK_FILE" ]]; then
  old_pid="$(cat "$LOCK_FILE" 2>/dev/null || true)"
  if [[ -n "${old_pid}" ]] && kill -0 "$old_pid" 2>/dev/null; then
    log "[SKIP] $CMD 已在运行 (PID=$old_pid)，跳过本轮"
    exit 0
  fi
  rm -f "$LOCK_FILE"
fi

echo $$ > "$LOCK_FILE"
cleanup() { rm -f "$LOCK_FILE"; }
trap cleanup EXIT

log "[START] main.py $CMD  (cwd=$ROOT)"
if command -v adb >/dev/null 2>&1; then
  log "adb devices:"
  adb devices 2>&1 | tee -a "$CRON_LOG"
else
  log "[WARN] PATH 中没有 adb"
fi

set +e
"$PY" main.py "$CMD" >>"$LOG_DIR/daily_${CMD}_stdout.log" 2>>"$LOG_DIR/daily_${CMD}_stderr.log"
code=$?
set -e

if [[ $code -eq 0 ]]; then
  log "[OK] main.py $CMD 结束 exit=$code"
else
  log "[FAIL] main.py $CMD 结束 exit=$code ，见 logs/daily_${CMD}_stderr.log"
fi
exit $code
