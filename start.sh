#!/bin/bash
# DirectionAI Agent 启动脚本
# 使用方法: ./start.sh [dev|build|help]

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DEERFLOW_BACKEND="$SCRIPT_DIR/backend"
DEERFLOW_FRONTEND="$SCRIPT_DIR/frontend"
VENV_PYTHON="$DEERFLOW_BACKEND/.venv/bin/python"
VENV_UV="$DEERFLOW_BACKEND/.venv/bin/uv"

export PATH="$HOME/.local/bin:$HOME/.npm-global/bin:$PATH"

cmd="${1:-dev}"

start_dev() {
    echo "=== 启动 DeerFlow 开发模式 ==="
    echo ""

    # 1. 检查 DeerFlow 依赖
    echo "[1/3] 检查 DeerFlow 后端依赖..."
    if ! "$VENV_PYTHON" -c "import deerflow" 2>/dev/null; then
        echo "  ⚠ DeerFlow 未安装，正在安装..."
        cd "$DEERFLOW_BACKEND"
        uv sync
        cd "$SCRIPT_DIR"
    else
        echo "  ✅ DeerFlow 已安装"
    fi

    # 2. 检查前端依赖
    echo "[2/3] 检查前端依赖..."
    if [ ! -d "$DEERFLOW_FRONTEND/node_modules" ]; then
        echo "  ⚠ 前端依赖未安装，正在安装..."
        cd "$DEERFLOW_FRONTEND"
        pnpm install
        cd "$SCRIPT_DIR"
    else
        echo "  ✅ 前端依赖已安装"
    fi

    # 3. 检查 pptagent
    echo "[3/3] 检查 pptagent..."
    if ! python -c "import fastapi, httpx, uvicorn" 2>/dev/null; then
        echo "  ⚠ pptagent 依赖未安装，正在安装..."
        pip install fastapi httpx uvicorn python-multipart sse-starlette
    else
        echo "  ✅ pptagent 依赖已安装"
    fi

    echo ""
    echo "=== 启动 DeerFlow (dev 模式) ==="
    echo "  DeerFlow 源码: $DEERFLOW_BACKEND"
    echo "  访问地址: http://localhost:2026"
    echo ""

    cd "$SCRIPT_DIR"
    make dev
}

start_build() {
    echo "=== 构建 DeerFlow 生产镜像 ==="
    cd "$SCRIPT_DIR"
    make start
}

show_help() {
    echo "DirectionAI Agent 启动脚本"
    echo ""
    echo "用法: $0 [命令]"
    echo ""
    echo "命令:"
    echo "  dev    开发模式（默认）- 热重载"
    echo "  build  生产模式构建"
    echo "  help   显示此帮助"
    echo ""
    echo "前置条件:"
    echo "  1. conda 环境已创建: conda create -n deer-flow python=3.12"
    echo "  2. 复制 .env 文件: cp .env.example .env"
    echo "  3. 安装 uv: curl -LsSf https://astral.sh/uv/install.sh | sh"
    echo ""
    echo "单独启动各服务:"
    echo "  # DeerFlow 后端 (deer-flow conda env)"
    echo "  cd backend && uv sync && make dev"
    echo ""
    echo "  # DeerFlow 前端"
    echo "  cd frontend && pnpm install && pnpm dev"
    echo ""
    echo "  # pptagent (direction conda env)"
    echo "  cd ../pptagent && python api.py"
    echo ""
}

case "$cmd" in
    dev)    start_dev ;;
    build)  start_build ;;
    help|--help|-h) show_help ;;
    *)       echo "未知命令: $cmd"; show_help; exit 1 ;;
esac
