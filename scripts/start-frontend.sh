#!/bin/bash
# 前端启动诊断和修复脚本

echo "======================================"
echo "云效 Bug Agent - 前端诊断"
echo "======================================"
echo ""

cd "$(dirname "$0")/../frontend"

# 1. 检查 node_modules
if [ ! -d "node_modules" ]; then
    echo "❌ node_modules 不存在"
    echo "📦 正在安装依赖..."
    npm install
    if [ $? -ne 0 ]; then
        echo "❌ 依赖安装失败"
        exit 1
    fi
    echo "✅ 依赖安装完成"
else
    echo "✅ node_modules 存在"
fi
echo ""

# 2. 检查端口占用
echo "🔍 检查端口 3001..."
if lsof -Pi :3001 -sTCP:LISTEN -t >/dev/null ; then
    echo "⚠️  端口 3001 被占用"
    echo "正在尝试释放端口..."
    lsof -ti:3001 | xargs kill -9 2>/dev/null || true
    sleep 2
    echo "✅ 端口已释放"
else
    echo "✅ 端口 3001 可用"
fi
echo ""

# 3. 启动开发服务器
echo "🚀 启动前端开发服务器..."
echo "访问地址: http://localhost:3001"
echo "按 Ctrl+C 停止服务"
echo ""
npm run dev
