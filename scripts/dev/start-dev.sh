#!/usr/bin/env bash
# FormuNex development helper: start/stop/status backend, frontend, worker
# Infrastructure (postgres, redis, kg) is managed via docker-compose.
# Assumes you are in the FormuNex root directory.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
RUN_DIR="${PROJECT_ROOT}/.run"
mkdir -p "${RUN_DIR}"

BACKEND_PIDFILE="${RUN_DIR}/backend.pid"
FRONTEND_PIDFILE="${RUN_DIR}/frontend.pid"
WORKER_PIDFILE="${RUN_DIR}/worker.pid"

# Ensure we are in project root
cd "${PROJECT_ROOT}"

# Helper to check if a pid file contains a running process
is_running() {
    local pidfile=$1
    if [[ -f "${pidfile}" ]]; then
        local pid
        pid=$(cat "${pidfile}" 2>/dev/null || true)
        if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
            return 0
        fi
    fi
    return 1
}

start_infra() {
    echo "Starting infrastructure containers (postgres, redis, kg)..."
    docker compose -f "${PROJECT_ROOT}/docker-compose.yml" -f "${PROJECT_ROOT}/docker-compose.dev.yml" up -d postgres redis kg
}

stop_infra() {
    echo "Stopping infrastructure containers..."
    docker compose -f "${PROJECT_ROOT}/docker-compose.yml" -f "${PROJECT_ROOT}/docker-compose.dev.yml" down
}

start_backend() {
    if is_running "${BACKEND_PIDFILE}"; then
        echo "Backend already running."
        return 0
    fi
    echo "Starting backend..."
    cd backend
    source .venv/bin/activate
    nohup uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload \
        > "${RUN_DIR}/backend.log" 2>&1 &
    echo $! > "${BACKEND_PIDFILE}"
    cd "${PROJECT_ROOT}"
    echo "Backend started (pid $(cat "${BACKEND_PIDFILE}"))."
}

start_frontend() {
    if is_running "${FRONTEND_PIDFILE}"; then
        echo "Frontend already running."
        return 0
    fi
    echo "Starting frontend..."
    cd frontend
    nohup npm run dev -- --host 0.0.0.0 \
        > "${RUN_DIR}/frontend.log" 2>&1 &
    echo $! > "${FRONTEND_PIDFILE}"
    cd "${PROJECT_ROOT}"
    echo "Frontend started (pid $(cat "${FRONTEND_PIDFILE}"))."
}

start_worker() {
    if is_running "${WORKER_PIDFILE}"; then
        echo "Worker already running."
        return 0
    fi
    echo "Starting Celery worker..."
    cd backend
    source .venv/bin/activate
    nohup celery -A app.worker.celery_app.celery_app worker \
        --concurrency=2 --prefetch-multiplier=1 --loglevel=info \
        > "${RUN_DIR}/worker.log" 2>&1 &
    echo $! > "${WORKER_PIDFILE}"
    cd "${PROJECT_ROOT}"
    echo "Worker started (pid $(cat "${WORKER_PIDFILE}"))."
}

start_all() {
    start_infra
    start_backend
    start_frontend
    start_worker
    echo "All services started."
}

stop_backend() {
    if is_running "${BACKEND_PIDFILE}"; then
        echo "Stopping backend..."
        kill "$(cat "${BACKEND_PIDFILE}")" || true
        rm -f "${BACKEND_PIDFILE}"
    else
        echo "Backend not running."
    fi
}

stop_frontend() {
    if is_running "${FRONTEND_PIDFILE}"; then
        echo "Stopping frontend..."
        kill "$(cat "${FRONTEND_PIDFILE}")" || true
        rm -f "${FRONTEND_PIDFILE}"
    else
        echo "Frontend not running."
    fi
}

stop_worker() {
    if is_running "${WORKER_PIDFILE}"; then
        echo "Stopping worker..."
        kill "$(cat "${WORKER_PIDFILE}")" || true
        rm -f "${WORKER_PIDFILE}"
    else
        echo "Worker not running."
    fi
}

stop_all() {
    stop_backend
    stop_frontend
    stop_worker
    # Optionally stop infra; comment out if you want to keep DB etc.
    # stop_infra
    echo "All app services stopped."
}

status() {
    echo "=== Infrastructure (docker-compose) ==="
    docker compose -f "${PROJECT_ROOT}/docker-compose.yml" -f "${PROJECT_ROOT}/docker-compose.dev.yml" ps postgres redis kg || true
    echo
    echo "=== Backend (uvicorn) ==="
    if is_running "${BACKEND_PIDFILE}"; then
        echo "running (pid $(cat "${BACKEND_PIDFILE}"))"
        tail -5 "${RUN_DIR}/backend.log" 2>/dev/null || true
    else
        echo "not running"
    fi
    echo
    echo "=== Frontend (vite) ==="
    if is_running "${FRONTEND_PIDFILE}"; then
        echo "running (pid $(cat "${FRONTEND_PIDFILE}"))"
        tail -5 "${RUN_DIR}/frontend.log" 2>/dev/null || true
    else
        echo "not running"
    fi
    echo
    echo "=== Worker (celery) ==="
    if is_running "${WORKER_PIDFILE}"; then
        echo "running (pid $(cat "${WORKER_PIDFILE}"))"
        tail -5 "${RUN_DIR}/worker.log" 2>/dev/null || true
    else
        echo "not running"
    fi
}

case "${1:-}" in
    start)
        start_all
        ;;
    stop)
        stop_all
        ;;
    restart)
        stop_all
        start_all
        ;;
    status)
        status
        ;;
    *)
        echo "Usage: $0 {start|stop|restart|status}"
        exit 1
        ;;
esac