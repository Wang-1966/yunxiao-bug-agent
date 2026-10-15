#!/bin/bash
# 云效 Bug 修复 Agent - 快速启动脚本

set -e

echo "======================================"
echo "云效 Bug 修复 Agent - 快速启动"
echo "======================================"
echo ""

# 检查当前目录
if [ ! -d "backend" ] || [ ! -d "frontend" ]; then
    echo "❌ 错误：请在项目根目录运行此脚本"
    exit 1
fi

# 1. 安装后端依赖
echo "📦 [1/4] 安装后端依赖..."
cd backend
if [ -n "${BACKEND_PYTHON:-}" ]; then
    if ! "$BACKEND_PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
        echo "❌ BACKEND_PYTHON 必须指向 Python 3.11+"
        exit 1
    fi
else
    for candidate in python3.11 python3.12 python3.13 python3.14 python3; do
        if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
            BACKEND_PYTHON=$(command -v "$candidate")
            break
        fi
    done
    if [ -z "${BACKEND_PYTHON:-}" ]; then
        echo "❌ 后端要求 Python 3.11+；请安装后重试，或通过 BACKEND_PYTHON 指定解释器路径"
        exit 1
    fi
fi

if [ ! -d "venv" ]; then
    echo "创建虚拟环境..."
    "$BACKEND_PYTHON" -m venv venv
fi

if ! venv/bin/python -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
    echo "❌ backend/venv 无效或 Python 版本低于 3.11；请移走旧环境，再运行此脚本重建"
    exit 1
fi
venv/bin/python -m pip install -r requirements.txt
echo "✅ 后端依赖安装完成"
echo ""

# 2. 安装前端依赖
echo "📦 [2/4] 安装前端依赖..."
cd ../frontend
if [ ! -d "node_modules" ]; then
    npm install
else
    echo "node_modules 已存在，跳过安装"
fi
echo "✅ 前端依赖安装完成"
echo ""

# 3. 启动后端
echo "🚀 [3/4] 启动后端服务..."
cd ../backend
BACKEND_STARTED=1
if lsof -nP -iTCP:8000 -sTCP:LISTEN -t >/dev/null 2>&1; then
    if curl -fsS http://localhost:8000/health > /dev/null 2>&1; then
        BACKEND_STARTED=0
        BACKEND_PID=$(lsof -tiTCP:8000 -sTCP:LISTEN | head -n 1)
        echo "✅ 后端服务已在运行，复用现有进程 (PID: $BACKEND_PID)"
    else
        echo "❌ 端口 8000 已被占用，但健康检查未通过"
        exit 1
    fi
else
    nohup venv/bin/python -m src.main > backend.log 2>&1 &
    BACKEND_PID=$!
    echo "✅ 后端服务已启动 (PID: $BACKEND_PID)"
fi
echo "   日志: backend/backend.log"
echo "   地址: http://localhost:8000"
echo ""

# 等待后端启动
if [ "$BACKEND_STARTED" -eq 1 ]; then
    echo "⏳ 等待后端启动..."
    sleep 5
fi

# 检查后端是否启动成功
if curl -fsS http://localhost:8000/health > /dev/null 2>&1; then
    echo "✅ 后端健康检查通过"
else
    echo "❌ 后端启动失败，请检查 backend/backend.log"
    if [ "$BACKEND_STARTED" -eq 1 ]; then
        kill "$BACKEND_PID" 2>/dev/null || true
    fi
    exit 1
fi
echo ""

# 4. 启动前端
echo "🚀 [4/4] 启动前端服务..."
cd ../frontend
npm run dev &
FRONTEND_PID=$!
echo "✅ 前端服务已启动 (PID: $FRONTEND_PID)"
echo ""

# 保存 PID
cd ..
if [ "$BACKEND_STARTED" -eq 1 ]; then
    echo "$BACKEND_PID" > .backend.pid
else
    rm -f .backend.pid
fi
echo "$FRONTEND_PID" > .frontend.pid

echo "======================================"
echo "✅ 启动完成！"
echo "======================================"
echo ""
echo "🌐 前端地址: http://localhost:3001"
echo "🔧 后端地址: http://localhost:8000"
echo "📚 API 文档: http://localhost:8000/docs"
echo ""
echo "💡 停止服务:"
echo "   ./scripts/stop.sh"
echo ""
echo "📝 查看日志:"
echo "   tail -f backend/backend.log"
echo ""

# 保持脚本运行
wait $FRONTEND_PID
