#!/usr/bin/env bash
#
# FormuNex 一键安装脚本 (macOS / Linux)
#
# 两种模式：
#   Docker 模式（默认，推荐）：PostgreSQL + Redis + Neo4j + 后端 + 前端 全部走 Docker。
#     FormuNex 在所有部署形态下都以 PostgreSQL 为主库，SQLite 仅用于 CI/单测。
#   Native 模式（--native）：本地 venv + npm + SQLite，仅用于开发调试。
#
# 用法:
#   ./install.sh                 # Docker 模式：检查 Docker → 配置 .env → 启动全部服务
#   ./install.sh --native        # Native 模式：安装依赖 → 配置 .env → SQLite 迁移
#   ./install.sh --native --start # Native 模式 + 安装后启动服务
#   ./install.sh -y              # 全自动（CI / 无人值守，Docker 模式）
#   ./install.sh --help          # 帮助
#
# 依赖：
#   Docker 模式：Docker + Docker Compose v2
#   Native 模式：Python 3.11+、Node.js 20+（可选，仅前端）
#
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
ASSUME_YES=0
DO_START=0
MODE="docker"

usage() {
  sed -n '3,18p' "$0"
}

for arg in "$@"; do
  case "$arg" in
    -y|--yes)   ASSUME_YES=1 ;;
    --start)    DO_START=1 ;;
    --native)   MODE="native" ;;
    --docker)   MODE="docker" ;;
    -h|--help)  usage; exit 0 ;;
    *) echo "未知参数: $arg"; usage; exit 1 ;;
  esac
done

# 版本比较：ver_ge "3.12.3" "3.11" → 0 表示 >=
ver_ge() {
  [ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -n1)" = "$2" ]
}

OS="linux"
[ "$(uname -s)" = "Darwin" ] && OS="macos"

# ---------------------------------------------------------------------------
# .env 配置（两种模式共用）
# ---------------------------------------------------------------------------
setup_env() {
  echo ""
  echo "==> 配置 .env"
  if [ -f "$ROOT/.env" ]; then
    echo "    .env 已存在，跳过"
  else
    cp "$ROOT/.env.example" "$ROOT/.env"
    echo "    已从 .env.example 生成 .env"
    echo "    内网/免登录：FORMUNEX_API_AUTH_ENABLED=false"
    echo "    LLM Key 可选（不填则用规则引擎离线运行）"
  fi
}

# ---------------------------------------------------------------------------
# Docker 模式
# ---------------------------------------------------------------------------
install_docker() {
  echo "==> [1/4] 环境检查（Docker 模式）"

  if ! command -v docker >/dev/null 2>&1; then
    echo "❌ 未找到 docker"
    if [ "$OS" = "macos" ]; then
      echo "  请安装 Docker Desktop：https://www.docker.com/products/docker-desktop/"
      echo "  或：brew install --cask docker"
    elif command -v apt >/dev/null 2>&1; then
      echo "  sudo apt update && sudo apt install -y docker.io docker-compose-v2"
    elif command -v dnf >/dev/null 2>&1; then
      echo "  sudo dnf install -y docker docker-compose"
    else
      echo "  参见 https://docs.docker.com/get-docker/"
    fi
    exit 1
  fi
  echo "    Docker $(docker --version | sed 's/Docker version //;s/,.*//') ✓"
  # 3-12：Docker Desktop 许可告知（用户必经的安装流程）。
  echo "    提示：使用 Docker Desktop 需遵守 Docker Desktop 许可条款，详见 Docker 官网法律条款页（docker.com/legal）。"

  if ! docker compose version >/dev/null 2>&1; then
    echo "❌ 未找到 docker compose v2（需要 Docker Compose v2，docker-compose v1 不可用）"
    echo "  Docker Desktop 自带 v2；Linux 请安装 docker-compose-v2 包"
    exit 1
  fi
  echo "    Docker Compose $(docker compose version --short) ✓"

  # Docker daemon 是否在运行
  if ! docker info >/dev/null 2>&1; then
    echo "❌ Docker daemon 未运行"
    if [ "$OS" = "macos" ]; then
      echo "  请启动 Docker Desktop 后重试"
    else
      echo "  sudo systemctl start docker"
    fi
    exit 1
  fi
  echo "    Docker daemon 运行中 ✓"

  setup_env

  echo ""
  echo "==> [2/4] 拉取镜像并启动服务"
  echo "    包含：PostgreSQL 16 + Redis 7 + Neo4j 5 + 后端 API + Celery worker + 前端"
  echo "    首次拉取约 1-2GB，取决于网络速度"
  cd "$ROOT"
  docker compose pull postgres redis kg backend worker beat frontend 2>&1 | tail -5
  docker compose up -d postgres redis kg
  echo "    等待数据库就绪..."
  for i in $(seq 1 30); do
    if docker compose exec -T postgres pg_isready -U "${FORMUNEX_PG_USER:-formunex}" -d "${FORMUNEX_PG_DB:-formunex}" >/dev/null 2>&1; then
      echo "    PostgreSQL 就绪 ✓"
      break
    fi
    if [ "$i" = "30" ]; then
      echo "❌ PostgreSQL 30 秒内未就绪，请检查 docker compose logs postgres"
      exit 1
    fi
    sleep 1
  done

  echo ""
  echo "==> [3/4] 数据库迁移"
  docker compose run --rm backend alembic upgrade head
  echo "    数据库已就绪 ✓"

  echo ""
  echo "==> [4/4] 启动全部服务"
  docker compose up -d backend worker beat frontend
  echo "    等待后端健康检查..."
  for i in $(seq 1 30); do
    if curl -sf http://localhost:8000/health >/dev/null 2>&1; then
      echo "    后端健康检查通过 ✓"
      break
    fi
    if [ "$i" = "30" ]; then
      echo "⚠️  后端 30 秒内未就绪，请检查 docker compose logs backend"
      break
    fi
    sleep 1
  done

  echo ""
  echo "==> 完成 ✅"
  echo ""
  echo "  前端:    http://localhost:5173"
  echo "  后端 API: http://localhost:8000"
  echo "  健康检查: curl -s localhost:8000/health"
  echo "  查看日志: docker compose logs -f [backend|worker|frontend]"
  echo "  停止服务: docker compose down"
  echo "  停止并清数据: docker compose down -v  （⚠️ 会删除 PostgreSQL 数据）"
  echo ""
  echo "  首次使用：浏览器打开 http://localhost:5173，按向导创建管理员账号"
}

# ---------------------------------------------------------------------------
# Native 模式（开发调试）
# ---------------------------------------------------------------------------
install_native() {
  echo "==> [1/5] 环境检查（Native 模式）"

  if ! command -v python3 >/dev/null 2>&1; then
    echo "❌ 未找到 python3"
    echo "  请先安装 Python 3.11+："
    if [ "$OS" = "macos" ]; then
      echo "    brew install python@3.12"
    elif command -v apt >/dev/null 2>&1; then
      echo "    sudo apt update && sudo apt install -y python3 python3-venv python3-pip"
    elif command -v dnf >/dev/null 2>&1; then
      echo "    sudo dnf install -y python3 python3-pip"
    elif command -v pacman >/dev/null 2>&1; then
      echo "    sudo pacman -S python python-pip"
    else
      echo "    参见 https://www.python.org/downloads/"
    fi
    exit 1
  fi
  PY_VER="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")')"
  if ! ver_ge "$PY_VER" "3.11"; then
    echo "❌ Python 版本过低: $PY_VER（需要 >= 3.11）"
    exit 1
  fi
  echo "    Python $PY_VER ✓"
  if ver_ge "$PY_VER" "3.14"; then
    echo "    ⚠ Python $PY_VER 比本项目测试过的版本（3.11–3.13）新；科学依赖可能没有预编译包。"
  fi

  if command -v node >/dev/null 2>&1; then
    NODE_VER="$(node --version | sed 's/^v//')"
    if ver_ge "$NODE_VER" "20"; then
      echo "    Node.js v$NODE_VER ✓"
    else
      echo "⚠️  Node.js v$NODE_VER 过低（需要 >= 20），前端安装可能失败"
    fi
    command -v npm >/dev/null 2>&1 || { echo "❌ 找到 node 但未找到 npm"; exit 1; }
  else
    echo "⚠️  未找到 node — 将跳过前端安装（只跑 API 不受影响）"
  fi

  command -v git >/dev/null 2>&1 || echo "⚠️  未找到 git（不影响安装，推送代码时需要）"

  echo ""
  echo "==> [2/5] 安装后端/前端依赖"
  bash "$ROOT/scripts/install.sh"

  setup_env

  echo ""
  echo "==> [4/5] 数据库迁移（SQLite，仅开发用；生产请用 Docker 模式的 PostgreSQL）"
  cd "$ROOT/backend"
  "$ROOT/backend/.venv/bin/python" -m alembic upgrade head
  echo "    数据库已就绪"

  echo ""
  echo "==> [4.5/5] Embedding 模型选择"
  echo "    选择向量模型（影响中英文检索质量）："
  echo "    1) MiniLM-L6-v2 (默认, 90MB, 英文优先, 轻量)"
  echo "    2) bge-small-zh-v1.5 (100MB, 中文)"
  echo "    3) bge-m3 (推荐, ~2GB, 多语言, 解决跨语言召回)"
  echo "    4) Qwen3-Embedding-0.6B (~1.2GB, 多语言)"
  echo "    5) m3e-base (~200MB, 中文)"
  if [ "$ASSUME_YES" = "1" ]; then
    EMB_CHOICE="1"
    echo "    全自动模式：使用默认 MiniLM-L6-v2"
  else
    read -rp "    请选择 [1-5, 默认1]: " EMB_CHOICE
    EMB_CHOICE="${EMB_CHOICE:-1}"
  fi
  case "$EMB_CHOICE" in
    1) EMB_MODEL="sentence-transformers/all-MiniLM-L6-v2" ;;
    2) EMB_MODEL="BAAI/bge-small-zh-v1.5" ;;
    3) EMB_MODEL="BAAI/bge-m3" ;;
    4) EMB_MODEL="Qwen/Qwen3-Embedding-0.6B" ;;
    5) EMB_MODEL="moka-ai/m3e-base" ;;
    *) EMB_MODEL="sentence-transformers/all-MiniLM-L6-v2" ;;
  esac
  if grep -q "^FORMUNEX_EMBEDDING_MODEL=" "$ROOT/.env" 2>/dev/null; then
    sed -i.bak "s|^FORMUNEX_EMBEDDING_MODEL=.*|FORMUNEX_EMBEDDING_MODEL=$EMB_MODEL|" "$ROOT/.env"
  else
    echo "FORMUNEX_EMBEDDING_MODEL=$EMB_MODEL" >> "$ROOT/.env"
  fi
  echo "    已选择: $EMB_MODEL"
  if [[ "$EMB_MODEL" == "BAAI/bge-m3" || "$EMB_MODEL" == "Qwen/Qwen3-Embedding-0.6B" ]]; then
    if [ "$ASSUME_YES" = "0" ]; then
      read -rp "    模型约 1-2GB，是否现在下载？[y/N] " dl_ans
      if [[ "$dl_ans" =~ ^[Yy]$ ]]; then
        echo "    下载中..."
        "$ROOT/backend/.venv/bin/python" -c "
from huggingface_hub import snapshot_download
snapshot_download('$EMB_MODEL', allow_patterns=['*.json','*.txt','*.model','*.safetensors','*.bin'])
print('    下载完成')
"
      else
        echo "    已跳过下载，首次使用时自动下载"
      fi
    fi
  fi

  echo ""
  echo "==> [5/5] 完成 ✅"
  echo ""
  echo "  后端 API:    cd backend && source .venv/bin/activate && uvicorn app.main:app --port 8000"
  echo "  Celery worker: cd backend && celery -A app.worker.celery_app worker --loglevel=info"
  echo "  前端:        cd frontend && npm run dev    # http://localhost:5174"
  echo "  健康检查:    curl -s localhost:8000/health"
  echo ""
  echo "  产品级运行（PostgreSQL + Redis + Neo4j）请用 Docker 模式："
  echo "    ./install.sh"
  echo "  详见 docs/QUICKSTART.md"

  SHOULD_START=0
  if [ "$DO_START" = "1" ]; then
    SHOULD_START=1
  elif [ "$ASSUME_YES" = "0" ] && [ -t 0 ]; then
    read -rp "是否现在一键启动全部服务？[y/N] " ans
    [[ "$ans" =~ ^[Yy]$ ]] && SHOULD_START=1
  fi

  if [ "$SHOULD_START" = "1" ]; then
    echo ""
    bash "$ROOT/scripts/start_all.sh"
  else
    echo ""
    echo "  随时一键启动：bash scripts/start_all.sh"
  fi
}

# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
if [ "$MODE" = "docker" ]; then
  install_docker
else
  install_native
fi
