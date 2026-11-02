#!/usr/bin/env bash
# FormuNex 一键启动：Redis + 后端(celery+uvicorn) + 前端(vite)
#   + MolScribe OCSR worker（离线结构识别）
#
# 用法：
#   bash scripts/start_all.sh    # 全栈 + MolScribe OCSR worker
#
# ⚠️ 内存提示：MolScribe worker 峰值 ~1.9GB（torch-cpu）。
#    停止/重启请用 scripts/stop_all.sh。
#
# 核心依赖（产品必达）：Redis :6379 + Celery worker（台账为 native，无需外部 ELN）
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOGS="$ROOT/logs"
mkdir -p "$LOGS"

# 绝对路径 DB：消除 cwd 依赖（celery/uvicorn 必须连同一个库文件）。
# 相对路径 sqlite:///./data/formunex.db 会因进程 cwd 不同而解析到不同文件，
# 曾导致 loop_history 写入旧库 /root/FormuNex/data/formunex.db 而前端读新库。
export FORMUNEX_DB_URL="${FORMUNEX_DB_URL:-sqlite:///$ROOT/backend/data/formunex.db}"

# Product defaults if unset: native ledger, real worker (not eager).
export FORMUNEX_CAMPAIGN_BACKEND="${FORMUNEX_CAMPAIGN_BACKEND:-native}"
export FORMUNEX_EXPERIMENT_BACKEND="${FORMUNEX_EXPERIMENT_BACKEND:-native}"
export FORMUNEX_CELERY_EAGER="${FORMUNEX_CELERY_EAGER:-false}"

echo "==> 1/5 Redis"
if ! redis-cli ping >/dev/null 2>&1; then
  if command -v redis-server >/dev/null 2>&1; then
    redis-server --daemonize yes --dir /var/lib/redis
    echo "    Redis 已启动"
  else
    echo "❌ Redis 未运行且找不到 redis-server。请先安装/启动 Redis（:6379）。"
    exit 1
  fi
else
  echo "    Redis 已在运行"
fi

echo "==> 2/5 台账（native，无需外部 ELN；外部 ELN 如 eLabFTW 走 lab 连接器同步）"

echo "==> 3/5 后端 celery worker"
CELERY_BIN="$ROOT/backend/.venv/bin/celery"
(
  cd "$ROOT/backend"
  FORMUNEX_CELERY_EAGER=false nohup "$CELERY_BIN" -A app.worker.celery_app worker --loglevel=info \
    >>"$LOGS/celery.log" 2>&1 &
)

echo "==> 4/5 后端 uvicorn"
UVICORN_BIN="$ROOT/backend/.venv/bin/uvicorn"
(
  cd "$ROOT/backend"
  FORMUNEX_CELERY_EAGER=false nohup "$UVICORN_BIN" app.main:app --host 127.0.0.1 --port 8000 --reload \
    >>"$LOGS/uvicorn.log" 2>&1 &
)

echo "==> 5/5 前端 vite"
(
  cd "$ROOT/frontend"
  nohup npx vite --force --host 0.0.0.0 --port 5174 >>"$LOGS/vite.log" 2>&1 &
)

# MolScribe OCSR worker（torch-cpu ~1.9GB，可选）
MOLSCRIBE_VENV="$ROOT/.venv-molscribe"
if [ ! -x "$MOLSCRIBE_VENV/bin/celery" ]; then
  echo "==> MolScribe OCSR worker — 跳过（$MOLSCRIBE_VENV 不存在，先跑 bash scripts/setup_molscribe_worker.sh 安装）"
else
  echo "==> MolScribe OCSR worker"
  (
    cd "$ROOT/backend"
    FORMUNEX_CELERY_EAGER=false \
      nohup "$MOLSCRIBE_VENV/bin/celery" -A app.worker.celery_app worker \
      -n molscribe@%h --queues=molscribe --concurrency=1 --loglevel=info \
      >>"$LOGS/molscribe.log" 2>&1 &
  )
  echo "    MolScribe worker 已启动（队列 molscribe，并发 1）"
fi

echo ""
echo "✅ 全部启动完成。日志目录：$LOGS/"
echo "   全栈清单：Redis + Celery worker + API + 前端"
echo "   后端健康检查：curl http://127.0.0.1:8000/health"
echo "   期望 /health：task_broker.reachable=true"
echo "   前端入口：http://127.0.0.1:5174"
