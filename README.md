# 云效 Bug 修复 Agent

一个基于 Web UI 的自主 Bug 修复系统，支持多 Agent 并发工作。Agent 使用可配置的 LLM（Claude/Grok/GPT）来分析和修复代码，用户通过可视化界面管理修复流程。

## ⭐ 核心特性

- 🌐 **Web UI 界面**：可视化操作，实时查看所有 agent 状态
- 🤖 **多 Agent 并发**：同时运行 3-5 个 agent，大幅提升效率
- 🎛️ **可配置 LLM**：支持 Claude/Grok/GPT，不同阶段用不同模型优化成本
- 🔍 **深度分析**：不猜测根因，不确定时使用日志确认
- 🎯 **追求最优**：提供最优方案而非最快修复
- ✅ **全面验证**：确保修复不影响其他功能
- 🎮 **保留决策权**：关键决策点需要你确认
- 💾 **状态持久化**：支持断点恢复
- 📊 **实时通信**：WebSocket 推送状态更新

## 💡 核心理解

**Agent 不是去"调用"Claude Code 或 Grok Build，而是自己实现相同的能力！**

```
Claude Code = Claude Opus 5 + 工具调用
Grok Build  = Grok 4.7 + 工具调用
本项目     = 可配置的 LLM + 工具集 + Web UI + 多并发
```

**Agent 拥有和这些工具完全相同的能力，但提供了：**
- ✅ Web UI 可视化操作（而不是 CLI）
- ✅ 多 Agent 并发修复（而不是单线程）
- ✅ 灵活的 LLM 配置（而不是固定模型）
- ✅ 云效自动集成（而不是手动）
- ✅ 成本优化（不同阶段用不同模型）

## 快速开始

### 环境要求

- Node.js 18+
- Python 3.11+
- Git

### 安装和运行

#### 1. 后端
```bash
cd backend
pip install -r requirements.txt

# 配置环境变量
export ANTHROPIC_API_KEY="your-claude-api-key"
export GROK_API_KEY="your-grok-relay-api-key"

# 启动后端
python -m src.main
# 访问 http://localhost:8000
```

#### 2. 前端
```bash
cd frontend
npm install

# 启动前端
npm run dev
# 访问 http://localhost:3000
```

### 配置 LLM

1. 打开 Web UI：http://localhost:3000
2. 进入"设置" → "LLM 配置"
3. 配置 API Key：
   - Claude API Key（如果使用 Claude）
   - Grok 中转站 URL 和 API Key（如果使用 Grok）
4. 选择模型配置：
   - 简单模式：选择一个默认模型
   - 混合模式：不同阶段用不同模型（推荐，节省 60% 成本）
5. 点击"测试连接"验证
6. 保存配置

### 使用

1. **Dashboard**：查看 bug 统计和 agent 状态
2. **Bug 列表**：点击"修复"按钮启动 agent
3. **Agent 监控**：实时查看修复进度
4. **决策确认**：在关键决策点进行确认
5. **完成**：查看修复结果，更新云效状态

## 工作流程

```
1. 获取 Bug → 2. 深度分析 → 3. 设计方案 → 4. 实施修复 → 5. 全面验证
                    ↓                ↓                           ↓
                需要日志确认      等待你确认方案         验证是否通过
```

**每个阶段：**
- **获取 Bug**：从云效自动获取，或手动选择
- **深度分析**：理解触发条件和现象，定位根因（置信度低时建议添加日志）
- **设计方案**：提供多个方案对比，推荐最优方案（不是最快修复）
- **实施修复**：修改代码、添加测试、创建 commit
- **全面验证**：运行测试、检查影响范围，确保不影响其他功能

## LLM 配置说明

### 支持的模型

| 模型 | 提供商 | 适用场景 | 成本 |
|------|--------|----------|------|
| Claude Opus 5 | Anthropic | 深度分析、复杂理解 | 高 |
| Claude Sonnet 4 | Anthropic | 快速验证、平衡性能 | 中 |
| Grok 4.7 | xAI (中转站) | 代码生成、方案设计 | 低 |
| GPT-4 | OpenAI | 通用备选 | 高 |

### 推荐配置（成本优化）

```yaml
分析根因: Claude Opus 5   # 需要深度理解，用最强的
设计方案: Grok 4.7        # 生成方案，性价比高
实施修复: Grok 4.7        # 写代码，性价比高
验证修复: Claude Sonnet 4 # 快速验证，便宜
```

**成本对比：**
- Claude Opus 5 全程: ~$0.50/bug
- **混合配置: ~$0.20/bug (节省 60%)**
- Grok 4.7 全程: ~$0.15/bug

### Grok 中转站配置

由于 xAI 可能没有公开 API，需要通过中转站访问：

1. 获取中转站信息（URL 和 API Key）
2. 在 Web UI 的"LLM 配置"页面填入：
   - Base URL: `https://your-relay.com/v1`
   - API Key: 你的中转站密钥
   - 模型名称: `grok-4.7`（或中转站指定的）
3. 点击"测试连接"验证

## 文档

- [产品需求文档 (PRD)](docs/PRD.md)
- [技术设计文档 (TDD)](docs/TDD.md)
- [用户手册](docs/USER_GUIDE.md) (待完成)
- [开发指南](docs/DEVELOPMENT.md) (待完成)

## 技术栈

**前端：**
- React 18 + TypeScript
- Ant Design 5 - UI 组件库
- Zustand - 状态管理
- TanStack Query - 数据请求
- WebSocket - 实时通信
- Vite - 构建工具

**后端：**
- FastAPI - Web 框架
- LangGraph - Agent 状态机
- 多 LLM 支持 - Claude/Grok/GPT
- 云效 MCP - Bug 平台集成
- SQLite - 数据持久化
- asyncio.Queue - 任务队列

**工具：**
- Tree-sitter - 代码分析
- GitPython - Git 操作
- subprocess - 测试执行

## 项目结构

```
yunxiao-bug-agent/
├── frontend/              # React 前端
│   ├── src/
│   │   ├── pages/         # 页面组件
│   │   │   ├── Dashboard/
│   │   │   ├── BugList/
│   │   │   ├── AgentMonitor/
│   │   │   └── Settings/
│   │   ├── components/    # 通用组件
│   │   ├── stores/        # 状态管理
│   │   ├── hooks/         # 自定义 Hooks
│   │   └── api/           # API 调用
│   └── package.json
├── backend/               # FastAPI 后端
│   ├── src/
│   │   ├── api/           # API 路由
│   │   ├── core/          # 调度器
│   │   ├── agent/         # Agent 实现
│   │   ├── llm/           # LLM 客户端
│   │   ├── tools/         # 工具集成
│   │   └── models/        # 数据模型
│   └── requirements.txt
├── docs/                  # 文档
│   ├── PRD.md            # 产品需求文档
│   ├── TDD.md            # 技术设计文档
│   └── SUMMARY.md        # 文档总结
└── README.md
```

## 开发进度

- [x] 项目结构和文档
- [x] 后端基础框架（35+ 文件，2500+ 行代码）
- [x] 前端完整实现（30+ 文件，完整 UI）
- [x] LLM 统一架构（支持 Claude/Grok 中转 API）
- [x] LangGraph Agent 状态机（7 个节点）
- [x] 多 Agent 调度器（并发 + 任务队列）
- [x] WebSocket 实时通信
- [x] Git Worktree 隔离
- [ ] 依赖安装和服务启动
- [ ] 端到端集成测试
- [ ] 云效 MCP 集成测试
- [ ] 优化和完善

**当前状态**: ✅ 核心框架完成，等待安装依赖和测试

## 贡献

欢迎提交 Issue 和 Pull Request！

## 许可证

MIT License
