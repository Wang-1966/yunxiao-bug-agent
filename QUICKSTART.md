# 云效 Bug 修复 Agent - 快速开始指南

## 📋 前提条件

- Python 3.9+
- Node.js 18+
- Git
- 云效账号和 API Token

## 🚀 快速启动

### 方式一：使用启动脚本（推荐）

```bash
# 1. 配置环境变量
cd backend
cp .env.example .env
# 编辑 .env 文件，填入实际的 API 配置

# 2. 一键启动
cd ..
chmod +x scripts/*.sh
./scripts/start.sh
```

### 方式二：手动启动

#### 1. 配置环境变量

```bash
cd backend
cp .env.example .env
# 编辑 .env，填入以下配置：
# - CLAUDE_RELAY_BASE_URL 和 CLAUDE_RELAY_API_KEY
# - GROK_RELAY_BASE_URL 和 GROK_RELAY_API_KEY
# - YUNXIAO_ACCESS_TOKEN 和其他云效配置
```

#### 2. 启动后端

```bash
cd backend

# 创建虚拟环境
python3 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 安装依赖
pip3 install -r requirements.txt

# 启动服务
python3 -m src.main
```

后端将运行在 http://localhost:8000

#### 3. 启动前端

打开新终端：

```bash
cd frontend

# 安装依赖
npm install

# 启动开发服务器
npm run dev
```

前端将运行在 http://localhost:3000

## 🎯 访问应用

- **前端界面**: http://localhost:3000
- **后端 API**: http://localhost:8000
- **API 文档**: http://localhost:8000/docs
- **健康检查**: http://localhost:8000/api/health

## 📖 主要功能

### 1. 仪表盘
- 查看 Bug 统计（总数、严重程度分布）
- 查看活跃 Agent 状态
- 查看最近修复记录

### 2. Bug 管理
- 从云效同步 Bug 列表
- 创建新 Bug
- 筛选和搜索 Bug
- 提交 Bug 修复任务

### 3. Agent 监控
- 实时查看 Agent 状态（空闲/运行中/等待决策）
- 查看当前执行阶段和进度
- 实时日志流
- 控制 Agent（暂停/恢复/停止）

### 4. LLM 配置
- 管理 LLM Provider（Claude、Grok）
- 配置不同阶段使用的模型
- 测试 API 连接
- 预估修复成本

## 🔧 配置说明

### LLM Provider 配置

系统支持两种 LLM Provider：

**Claude（通过中转 API）**
```env
CLAUDE_RELAY_BASE_URL=https://your-claude-relay.com/v1
CLAUDE_RELAY_API_KEY=sk-xxx
CLAUDE_DEFAULT_MODEL=claude-opus-5
```

**Grok（通过中转 API）**
```env
GROK_RELAY_BASE_URL=https://your-grok-relay.com/v1
GROK_RELAY_API_KEY=grok-xxx
GROK_DEFAULT_MODEL=grok-2-latest
```

### 阶段化配置（可选）

可以为不同阶段配置不同的模型以优化成本：

- **analyze（分析）**: 使用强大模型（如 claude-opus-5）
- **propose（提案）**: 使用强大模型
- **fix（修复）**: 可使用较便宜的模型（如 grok-2）
- **verify（验证）**: 使用强大模型

配置通过前端 UI 的"LLM 配置"页面进行。

### 云效配置

```env
YUNXIAO_API_BASE=https://devops.aliyun.com
YUNXIAO_ACCESS_TOKEN=your-token
YUNXIAO_ORG_ID=your-org-id
YUNXIAO_PROJECT_ID=your-project-id
```

获取 Token：云效控制台 → 个人设置 → Access Token

## 📝 使用流程

### 修复单个 Bug

1. 进入"Bug 列表"页面
2. 点击"同步云效"获取最新 Bug
3. 选择一个 Bug，点击"提交修复"
4. 系统分配空闲 Agent 开始修复
5. 在"Agent 监控"页面实时查看进度
6. 当 Agent 需要决策时，会弹出决策框
7. 做出选择后，Agent 继续执行
8. 修复完成后，查看结果和生成的 PR

### 并发修复多个 Bug

1. 在 Bug 列表勾选多个 Bug
2. 点击"批量修复"
3. 系统将任务加入队列
4. 最多 3 个 Agent 同时工作
5. 每个 Agent 在独立的 Git Worktree 中工作，互不干扰

## 🛠️ 开发指南

### 后端开发

```bash
cd backend

# 运行测试
pytest tests/

# 格式化代码
black src/

# 类型检查
mypy src/
```

### 前端开发

```bash
cd frontend

# 开发服务器（热重载）
npm run dev

# 构建生产版本
npm run build

# 预览生产构建
npm run preview

# 类型检查
npm run type-check
```

## 📊 技术架构

### 后端技术栈
- **FastAPI** - 异步 Web 框架
- **LangGraph** - Agent 状态机
- **SQLAlchemy** - ORM
- **SQLite** - 数据库
- **GitPython** - Git 操作
- **WebSocket** - 实时通信

### 前端技术栈
- **React 18** - UI 框架
- **TypeScript** - 类型安全
- **Ant Design 5** - UI 组件库
- **Zustand** - 状态管理
- **Axios** - HTTP 客户端
- **Vite** - 构建工具

## 🐛 常见问题

### Q: 后端启动失败，提示数据库错误
A: 检查 `backend/data/` 目录是否存在，如果不存在需要手动创建：
```bash
mkdir -p backend/data
```

### Q: 前端无法连接后端
A: 检查后端是否正常运行，访问 http://localhost:8000/api/health 应该返回 OK

### Q: Agent 修复失败，提示 Git 错误
A: 检查项目是否是 Git 仓库，检查 `.worktrees/` 目录权限

### Q: LLM API 调用失败
A: 检查 `.env` 文件中的 API 配置是否正确，特别是 base_url 和 api_key

### Q: WebSocket 连接失败
A: 检查浏览器控制台错误，确认后端 WebSocket 端点正常（ws://localhost:8000/ws）

## 🔄 停止服务

### 使用停止脚本
```bash
./scripts/stop.sh
```

### 手动停止
```bash
# 停止后端
pkill -f "uvicorn.*src.main"

# 停止前端
pkill -f "vite.*frontend"
```

## 📚 更多文档

- [产品需求文档](docs/PRD.md)
- [技术设计文档](docs/TDD.md)
- [LLM 配置说明](docs/LLM_CONFIG_V2.md)
- [开发进度](PROGRESS.md)

## 📄 License

MIT

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！
