#!/bin/bash
# 设置符号链接脚本
# 运行方式: ./setup_symlinks.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$SCRIPT_DIR"

echo "=== 设置符号链接 ==="

# DeerFlow 源码（与 DirectionAI-Agent 同级的 deer-flow-github 目录）
DEER_FLOW_GITHUB="$PROJECT_DIR/../deer-flow-github"

if [ ! -d "$DEER_FLOW_GITHUB" ]; then
    echo "⚠ 找不到 deer-flow-github 目录: $DEER_FLOW_GITHUB"
    echo "  请确保 deer-flow-github 在 DirectionAI-Agent 同级目录"
    exit 1
fi

# PPT 生成服务（与 DirectionAI-Agent 同级的 pptagent 目录）
PPTAGENT="$PROJECT_DIR/../pptagent"

if [ ! -d "$PPTAGENT" ]; then
    echo "⚠ 找不到 pptagent 目录: $PPTAGENT"
    exit 1
fi

# 设置符号链接
ln -sfn "$DEER_FLOW_GITHUB/backend" "$PROJECT_DIR/backend"
echo "  ✅ backend -> $DEER_FLOW_GITHUB/backend"

ln -sfn "$DEER_FLOW_GITHUB/frontend" "$PROJECT_DIR/frontend"
echo "  ✅ frontend -> $DEER_FLOW_GITHUB/frontend"

ln -sfn "$PPTAGENT" "$PROJECT_DIR/pptagent"
echo "  ✅ pptagent -> $PPTAGENT"

echo ""
echo "=== 符号链接设置完成 ==="
echo "  DeerFlow 后端: $PROJECT_DIR/backend"
echo "  DeerFlow 前端: $PROJECT_DIR/frontend"
echo "  PPT 生成服务: $PROJECT_DIR/pptagent"
