#!/bin/bash
# 云效 Bug 修复 Agent - 停止服务脚本

set -e

echo "======================================"
echo "云效 Bug 修复 Agent - 停止服务"
echo "======================================"
echo ""

cd "$(dirname "$0")/.."

# 停止后端
if [ -f ".backend.pid" ]; then
    BACKEND_PID=$(cat .backend.pid)
    if ps -p $BACKEND_PID > /dev/null 2>&1; then
        echo "🛑 停止后端服务 (PID: $BACKEND_PID)..."
        kill $BACKEND_PID
        echo "✅ 后端服务已停止"
    else
        echo "⚠️  后端服务未运行"
    fi
    rm .backend.pid
else
    echo "⚠️  未找到后端 PID 文件"
fi
echo ""

# 停止前端
if [ -f ".frontend.pid" ]; then
    FRONTEND_PID=$(cat .frontend.pid)
    if ps -p $FRONTEND_PID > /dev/null 2>&1; then
        echo "🛑 停止前端服务 (PID: $FRONTEND_PID)..."
        kill $FRONTEND_PID
        echo "✅ 前端服务已停止"
    else
        echo "⚠️  前端服务未运行"
    fi
    rm .frontend.pid
else
    echo "⚠️  未找到前端 PID 文件"
fi
echo ""

# 清理可能残留的进程
echo "🧹 清理残留进程..."
pkill -f "uvicorn.*src.main" || true
pkill -f "vite.*frontend" || true

echo "======================================"
echo "✅ 所有服务已停止"
echo "======================================"
