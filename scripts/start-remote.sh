#!/usr/bin/env bash
# 远程机器启动脚本（无 Homebrew，使用 uv/nvm 安装的环境）
#
# 启动：
#   ./scripts/start-remote.sh
# 停止：
#   ./scripts/stop-all.sh
#
# 服务：
#   ASR (faster-whisper) → http://127.0.0.1:9000
#   FastAPI 后端         → http://127.0.0.1:8000
#   Vite 前端            → http://0.0.0.0:5173  （Tailscale 可访问）
#   Cloudflare Tunnel    → https://xxx.trycloudflare.com （公网访问）

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ASR_ROOT="$HOME/local-asr-service"
LOG_DIR="$ROOT/logs"
mkdir -p "$LOG_DIR"

ASR_PORT="${ASR_PORT:-9000}"
API_PORT="${API_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"

# 加载 nvm
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && source "$NVM_DIR/nvm.sh"

die() { echo "❌ $*" >&2; exit 1; }

port_listening() {
  lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1
}

wait_http() {
  local url="$1" name="$2" max="${3:-30}" i=0
  while (( i < max )); do
    curl -sS -o /dev/null --connect-timeout 1 "$url" 2>/dev/null && echo "✅ $name 已就绪" && return 0
    sleep 1; i=$((i+1))
  done
  echo "⚠️  $name 未在 ${max}s 内响应，请查看 $LOG_DIR/"
}

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  启动 douyin-project · $(hostname)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

[[ -d "$ROOT/.venv" ]]    || die "缺少后端 venv: $ROOT/.venv"
[[ -d "$ASR_ROOT/.venv" ]] || die "缺少 ASR venv: $ASR_ROOT/.venv"
[[ -d "$ROOT/frontend/node_modules" ]] || die "请先: cd frontend && npm install"

# ── ASR ──
if port_listening "$ASR_PORT"; then
  echo "⚠️  ASR 端口 $ASR_PORT 已占用，跳过"
else
  echo "🎙  启动 ASR :$ASR_PORT ..."
  cd "$ASR_ROOT"
  nohup ./.venv/bin/uvicorn main:app --host 127.0.0.1 --port "$ASR_PORT" \
    >"$LOG_DIR/asr.log" 2>&1 &
  echo $! >"$LOG_DIR/asr.pid"
  cd "$ROOT"
  sleep 2
fi

# ── FastAPI 后端 ──
if port_listening "$API_PORT"; then
  echo "⚠️  后端端口 $API_PORT 已占用，跳过"
else
  echo "🚀 启动后端 :$API_PORT ..."
  cd "$ROOT"
  nohup ./.venv/bin/uvicorn app:app --host 0.0.0.0 --port "$API_PORT" \
    >"$LOG_DIR/api.log" 2>&1 &
  echo $! >"$LOG_DIR/api.pid"
  sleep 2
fi

# ── Vite 前端 ──
if port_listening "$FRONTEND_PORT"; then
  echo "⚠️  前端端口 $FRONTEND_PORT 已占用，跳过"
else
  echo "🌐 启动前端 :$FRONTEND_PORT ..."
  cd "$ROOT/frontend"
  nohup npm run dev -- --host 0.0.0.0 --port "$FRONTEND_PORT" \
    >"$LOG_DIR/frontend.log" 2>&1 &
  echo $! >"$LOG_DIR/frontend.pid"
  cd "$ROOT"
  sleep 2
fi

echo ""
echo "📎 访问地址（Tailscale IP: 100.125.41.71）"
echo "   前端:    http://100.125.41.71:$FRONTEND_PORT"
echo "   后端 API: http://100.125.41.71:$API_PORT"
echo "   ASR:     http://127.0.0.1:$ASR_PORT/transcribe"
echo ""
echo "📄 日志: $LOG_DIR/{asr,api,frontend}.log"
echo "🛑 停止: $ROOT/scripts/stop-all.sh"
echo ""

wait_http "http://127.0.0.1:$API_PORT/api/creators" "后端" 25 || true
wait_http "http://127.0.0.1:$FRONTEND_PORT/" "前端" 15 || true

# ── Cloudflare Tunnel（LaunchAgent — 崩溃自动重启）──
CLOUDFLARED="$HOME/.local/bin/cloudflared"
PLIST_LABEL="com.cloudflare.tunnel"
PLIST_DST="$HOME/Library/LaunchAgents/${PLIST_LABEL}.plist"

if [[ -x "$CLOUDFLARED" ]]; then
  mkdir -p "$HOME/Library/LaunchAgents"
  cat >"$PLIST_DST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${PLIST_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>${CLOUDFLARED}</string>
        <string>tunnel</string>
        <string>--no-autoupdate</string>
        <string>--config</string>
        <string>${HOME}/.cloudflared/config.yml</string>
        <string>run</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>NO_PROXY</key>
        <string>*</string>
        <key>no_proxy</key>
        <string>*</string>
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>${LOG_DIR}/tunnel.log</string>
    <key>StandardErrorPath</key>
    <string>${LOG_DIR}/tunnel.log</string>
    <key>ThrottleInterval</key>
    <integer>10</integer>
</dict>
</plist>
PLIST

  if launchctl list "$PLIST_LABEL" >/dev/null 2>&1; then
    launchctl unload "$PLIST_DST" 2>/dev/null || true
    launchctl load  "$PLIST_DST"
    echo "✅ Cloudflare Tunnel 已重新加载（崩溃自动重启已启用）"
  else
    launchctl load "$PLIST_DST"
    echo "✅ Cloudflare Tunnel 已启动（崩溃自动重启已启用）"
  fi
  echo "https://app.xiaoheiban.cc" >"$LOG_DIR/tunnel.url"
  echo "✅ 公网地址: https://app.xiaoheiban.cc"
fi

echo ""
echo "完成。查看公网地址: cat $LOG_DIR/tunnel.url"
