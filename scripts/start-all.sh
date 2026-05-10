#!/usr/bin/env bash
# 一键启动本地所需服务：
#   1) local-asr-service  → http://127.0.0.1:9000/transcribe
#   2) FastAPI (app.py)   → http://127.0.0.1:8000
#   3) Vite 前端          → http://127.0.0.1:5173
#
# 用法：
#   chmod +x scripts/start-all.sh
#   ./scripts/start-all.sh
#
# 可选环境变量：
#   LOCAL_ASR_ROOT   默认：与本项目同级的 ../local-asr-service
#   ASR_PORT         默认 9000
#   API_PORT         默认 8000
#   FRONTEND_PORT    默认 5173
#   NO_RELOAD=1      后端不使用 --reload（更稳定）
#
# 首次在新机器部署时，若「加载视频列表失败」，请在项目根目录执行：
#   .venv/bin/playwright install chromium

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ASR_ROOT="${LOCAL_ASR_ROOT:-$ROOT/../local-asr-service}"
LOG_DIR="$ROOT/logs"
mkdir -p "$LOG_DIR"

ASR_PORT="${ASR_PORT:-9000}"
API_PORT="${API_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"

die() { echo "❌ $*" >&2; exit 1; }

port_listening() {
  local p="$1"
  lsof -nP -iTCP:"$p" -sTCP:LISTEN >/dev/null 2>&1
}

wait_http() {
  local url="$1"
  local name="$2"
  local max="${3:-30}"
  local i=0
  while (( i < max )); do
    if curl -sS -o /dev/null --connect-timeout 1 "$url" 2>/dev/null; then
      echo "✅ $name 已就绪"
      return 0
    fi
    sleep 1
    i=$((i + 1))
  done
  echo "⚠️  $name 在 ${max}s 内未响应 $url ，请查看 logs/"
}

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  一键启动 · 项目根目录: $ROOT"
echo "  ASR 目录: $ASR_ROOT"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

[[ -d "$ROOT/.venv" ]] || die "未找到后端虚拟环境: $ROOT/.venv（请先 python3 -m venv .venv && pip install -r requirements.txt）"
[[ -f "$ROOT/app.py" ]] || die "未找到 $ROOT/app.py"
[[ -d "$ASR_ROOT" ]] || die "未找到 ASR 项目: $ASR_ROOT（可设置 LOCAL_ASR_ROOT）"
[[ -d "$ASR_ROOT/.venv" ]] || die "未找到 ASR 虚拟环境: $ASR_ROOT/.venv"
[[ -f "$ASR_ROOT/main.py" ]] || die "未找到 $ASR_ROOT/main.py"
[[ -d "$ROOT/frontend/node_modules" ]] || die "请先执行: cd frontend && npm install"

if port_listening "$ASR_PORT"; then
  echo "⚠️  端口 $ASR_PORT 已被占用，跳过启动 ASR（若需重启请先 ./scripts/stop-all.sh）"
else
  echo "🎙  启动 ASR :$ASR_PORT ..."
  (
    cd "$ASR_ROOT"
    nohup ./.venv/bin/uvicorn main:app --host 127.0.0.1 --port "$ASR_PORT" \
      >"$LOG_DIR/asr.log" 2>&1 &
    echo $! >"$LOG_DIR/asr.pid"
  )
  sleep 2
fi

if port_listening "$API_PORT"; then
  echo "⚠️  端口 $API_PORT 已被占用，跳过启动后端"
else
  echo "🚀 启动后端 :$API_PORT ..."
  RELOAD_FLAG=(--reload)
  if [[ "${NO_RELOAD:-0}" == "1" ]]; then
    RELOAD_FLAG=()
  fi
  (
    cd "$ROOT"
    nohup ./.venv/bin/uvicorn app:app --host 127.0.0.1 --port "$API_PORT" "${RELOAD_FLAG[@]}" \
      >"$LOG_DIR/api.log" 2>&1 &
    echo $! >"$LOG_DIR/api.pid"
  )
  sleep 2
fi

if port_listening "$FRONTEND_PORT"; then
  echo "⚠️  端口 $FRONTEND_PORT 已被占用，跳过启动前端"
else
  echo "🌐 启动前端 :$FRONTEND_PORT ..."
  (
    cd "$ROOT/frontend"
    nohup npm run dev -- --host 127.0.0.1 --port "$FRONTEND_PORT" \
      >"$LOG_DIR/frontend.log" 2>&1 &
    echo $! >"$LOG_DIR/frontend.pid"
  )
  sleep 2
fi

echo ""
echo "📎 访问地址"
echo "   前端:    http://127.0.0.1:$FRONTEND_PORT"
echo "   后端 API: http://127.0.0.1:$API_PORT"
echo "   ASR:     http://127.0.0.1:$ASR_PORT/transcribe"
echo ""
echo "📄 日志: $LOG_DIR/{asr,api,frontend}.log"
echo "🛑 停止: $ROOT/scripts/stop-all.sh"
echo ""

wait_http "http://127.0.0.1:$API_PORT/api/creators" "后端" 25 || true
wait_http "http://127.0.0.1:$FRONTEND_PORT/" "前端" 15 || true

echo "完成。"
