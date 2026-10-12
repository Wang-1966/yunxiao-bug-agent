#!/bin/bash
# 云效 Bug 修复 Agent - 快速启动脚本

set -e

echo "======================================"
echo "云效 Bug 修复 Agent - 快速启动"
echo "======================================"
echo ""

# 检查当前目录
if [ ! -f "PROGRESS.md" ]; then
    echo "❌ 错误：请在项目根目录运行此脚本"
    exit 1
fi

# 1. 安装后端依赖
echo "📦 [1/4] 安装后端依赖..."
cd backend
if [ ! -d "venv" ]; then
    echo "创建虚拟环境..."
    python3 -m venv venv
fi

source venv/bin/activate
pip3 install -r requirements.txt
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
source venv/bin/activate
nohup python3 -m src.main > backend.log 2>&1 &
BACKEND_PID=$!
echo "✅ 后端服务已启动 (PID: $BACKEND_PID)"
echo "   日志: backend/backend.log"
echo "   地址: http://localhost:8000"
echo ""

# 等待后端启动
echo "⏳ 等待后端启动..."
sleep 5

# 检查后端是否启动成功
if curl -s http://localhost:8000/api/health > /dev/null 2>&1; then
    echo "✅ 后端健康检查通过"
else
    echo "⚠️  后端可能未完全启动，请检查 backend/backend.log"
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
echo "$BACKEND_PID" > .backend.pid
echo "$FRONTEND_PID" > .frontend.pid

echo "======================================"
echo "✅ 启动完成！"
echo "======================================"
echo ""
echo "🌐 前端地址: http://localhost:3000"
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
