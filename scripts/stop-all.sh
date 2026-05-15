#!/usr/bin/env bash
# 停止 start-all.sh 启动的进程（通过 logs/*.pid）
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$ROOT/logs"

kill_pidfile() {
  local f="$1"
  local name="$2"
  if [[ -f "$f" ]]; then
    local pid
    pid="$(cat "$f" 2>/dev/null || true)"
    if [[ -n "${pid:-}" ]] && kill -0 "$pid" 2>/dev/null; then
      echo "🛑 停止 $name (pid=$pid)"
      kill "$pid" 2>/dev/null || true
      sleep 1
      kill -9 "$pid" 2>/dev/null || true
    fi
    rm -f "$f"
  fi
}

[[ -d "$LOG_DIR" ]] || { echo "无日志目录，无需停止"; exit 0; }

kill_pidfile "$LOG_DIR/frontend.pid" "前端"
kill_pidfile "$LOG_DIR/api.pid" "后端"
kill_pidfile "$LOG_DIR/asr.pid" "ASR"

# 停止 Cloudflare Tunnel LaunchAgent
PLIST_LABEL="com.cloudflare.tunnel"
PLIST_DST="$HOME/Library/LaunchAgents/${PLIST_LABEL}.plist"
if launchctl list "$PLIST_LABEL" >/dev/null 2>&1; then
  echo "🛑 停止 Cloudflare Tunnel (LaunchAgent)"
  launchctl unload "$PLIST_DST" 2>/dev/null || true
fi

echo "✅ 已尝试停止（若端口仍被占用，请手动结束占用进程）"
