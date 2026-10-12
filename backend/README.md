# 云效 Bug 修复 Agent - 后端

## 云效缺陷同步

可在前端「通用配置 → 云效 MCP 配置」保存连接信息，保存后立即生效。
配置保存在 `backend/data/yunxiao_config.json`，文件权限为仅当前用户可读写；Token 不回显，留空保存保留已有 Token。
该文件存在时优先使用其中的云效配置，否则继续读取 `backend/.env`。设置 `YUNXIAO_API_BASE=https://openapi-rdc.aliyuncs.com`、
`YUNXIAO_ACCESS_TOKEN`、实际 `YUNXIAO_ORG_ID`，以及 `YUNXIAO_PROJECT_ID`。
项目 ID 填 `*` 时遍历该组织下所有可访问项目；指定实际 ID 时只同步该项目。
Token 需要组织用户、项目、工作项的只读权限。`viewIdentifier` 是视图 ID，不是项目 ID。

`POST /api/bugs/sync` 分页查询 Token 所属用户负责的 Bug，包括历史已关闭记录。
完整读取成功后才在一个事务中更新本地数据库，返回 `total/created/updated/unchanged`。
重复同步保持本地修复状态、仓库路径、分支和 Agent 分配不变。
`source_status` 和 `source_project` 分别保留云效原始状态与项目名称。
新导入的已修复、已关闭、暂不修复记录标记为 `resolved`，其他记录标记为 `pending`。
本地录入时间是入库时间，非云效工作项创建时间。

验证：`python3 -m pytest tests/test_bug_sync.py -q`。

多 Agent 并发 Bug 修复系统后端服务

## 技术栈

- **框架**: FastAPI 0.115.0
- **Agent 框架**: LangGraph 0.2.45
- **数据库**: SQLite (aiosqlite)
- **LLM 客户端**: OpenAI SDK (统一接口)
- **代码分析**: tree-sitter
- **Git 操作**: GitPython
- **日志**: loguru

## 项目结构

```
backend/
├── src/
│   ├── main.py              # FastAPI 应用入口
│   ├── config.py            # 配置管理
│   ├── api/                 # API 路由
│   │   ├── bugs.py          # Bug 管理
│   │   ├── agents.py        # Agent 管理
│   │   ├── llm_config.py    # LLM 配置
│   │   └── websocket.py     # WebSocket 通信
│   ├── agent/               # LangGraph Agent
│   │   ├── state.py         # 状态定义
│   │   ├── nodes.py         # 节点实现
│   │   └── graph.py         # 状态图
│   ├── core/                # 核心逻辑
│   │   └── orchestrator.py  # 多 Agent 协调器
│   ├── llm/                 # LLM 抽象
│   │   ├── unified_client.py # 统一客户端
│   │   └── factory.py       # 客户端工厂
│   ├── tools/               # 工具集
│   │   ├── yunxiao/         # 云效 MCP 工具
│   │   ├── code_analyzer/   # 代码分析
│   │   ├── test_runner/     # 测试运行
│   │   └── git_operator/    # Git 操作
│   ├── models/              # 数据模型
│   └── db/                  # 数据库
│       ├── init.sql         # 初始化脚本
│       └── database.py      # 数据库连接
├── tests/                   # 测试
├── requirements.txt         # 依赖
└── README.md               # 本文件
```

## 快速开始

### 1. 安装依赖

```bash
cd backend
pip install -r requirements.txt
```

### 2. 配置环境变量

创建 `.env` 文件：

```env
# API 配置
API_HOST=0.0.0.0
API_PORT=8000

# CORS
CORS_ORIGINS=["http://localhost:3000", "http://localhost:5173"]

# 数据库
DATABASE_URL=sqlite+aiosqlite:///./data/yunxiao_agent.db

# Agent 配置
MAX_CONCURRENT_AGENTS=3
AGENT_TIMEOUT=1800

# 工作目录
WORKTREES_DIR=.worktrees

# 日志
LOG_LEVEL=INFO
```

### 3. 启动服务

```bash
python -m src.main
```

或使用 uvicorn：

```bash
mkdir -p .worktrees
uvicorn src.main:app --reload --reload-dir src --reload-exclude .worktrees --host 0.0.0.0 --port 8000
```

开发热重载必须排除修复工作树，否则工作树中的 Python 源码或依赖安装会重启后端并取消任务。
`python -m src.main` 会自动创建并排除 `WORKTREES_DIR`；直接使用 uvicorn 时，自定义工作树路径也需替换上述排除目录。

## API 文档

启动服务后访问：
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

## 核心功能

### 1. LLM 配置管理

支持多个 LLM 提供商（Claude、Grok 等），所有提供商使用 OpenAI 兼容格式：

```python
{
  "providers": [
    {
      "id": "claude",
      "name": "Claude (Relay)",
      "base_url": "https://relay.example.com/v1",
      "api_key": "your-api-key",
      "available_models": ["claude-opus-5"],
      "default_model": "claude-opus-5",
      "enabled": true
    }
  ],
  "use_stage_config": true,
  "stage_config": {
    "analyze": {"provider": "claude", "model": "claude-opus-5"},
    "fix": {"provider": "grok", "model": "grok-2"}
  }
}
```

### 2. 多 Agent 并发

使用 `MultiAgentOrchestrator` 管理多个 Agent 并发执行：

- 任务队列：优先级队列
- 并发控制：最多 3 个 Agent 同时运行
- Git Worktree 隔离：每个 Agent 独立工作树

### 3. LangGraph 状态机

Bug 修复流程：

```
fetch_bug → analyze_root_cause → [add_logging] → design_proposal → implement_fix → verify_fix
```

### 4. WebSocket 实时通信

- Agent 状态更新
- 日志流式输出
- 用户决策请求/响应

## 开发

### 运行测试

```bash
pytest tests/
```

### 代码格式化

```bash
black src/
ruff check src/
```

## 许可证

MIT
