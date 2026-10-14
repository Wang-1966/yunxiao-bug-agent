# 云效 Bug 修复 Agent - 后端

## 修复分支与 Git 记录

新任务的隔离分支使用 `agentfix/<Bug短ID>-<短标题>-<运行短ID>`，例如
`agentfix/c790db24-讲义历史样式异常-46bea39aadd6`。标题清理为合法分支字符并限制为 24 个字符，
Bug ID 取前 8 个字符，运行 ID 取前 12 个字符；完整 Bug 标题仍保存在任务中。
修复提交使用 `fix: <Bug标题>`，正文记录完整 Bug ID。确认修复后，合并记录显示
`Merge branch 'agentfix/...' into <目标分支>`，实际合并对象仍是已验证的完整提交 hash。
新规则仅应用于后续任务，不重命名已有分支或重写 Git 历史。清理根据任务记录和 Git 工作树注册信息进行，
同时识别已有 `bug-agent/` 分支，不依赖由分支名称拼接工作树路径。

## 人工验收预览

保留每次修复的独立工作树和分支。在「项目管理 → 配置仓库」填写开发与验收启动命令，
例如 `pnpm dev:test --host 127.0.0.1 --port {port} --strictPort`；子目录项目可使用
`cd frontend && pnpm dev --host 127.0.0.1 --port {port} --strictPort`。
`{port}` 必填，启动时替换为空闲端口。沿用自动验证阶段已安装的依赖，若验证没有安装依赖，
可配置 `pnpm install --frozen-lockfile && pnpm dev --host 127.0.0.1 --port {port} --strictPort`。
进程必须在前台运行，不能自行转后台，也不能忽略系统指定的端口。

按需填写预览环境文件相对路径（如 `.env.local`、`frontend/.env.local`）。预览启动前从项目仓库复制，
仅允许复制被工作树 Git 忽略且不超过 1 MB 的文件；拒绝越界路径与符号链接，不覆盖工作树已有文件。
这不会修改 Git 中的已验证代码；环境配置和外部 API/数据库仍是项目原有资源，并不提供数据隔离。

配置「固定入口端口」为原浏览器使用的端口（如 3000，也可选择其他端口）。首次接入时，
先停止占用此端口的手动开发服务，再点击项目列表中的「启动项目」。本地代理持有固定入口，
原项目在内部端口持续运行。后续通过项目列表启动项目，在原页面复现问题并启动 Agent 修复。
验收弹窗点击「启动预览」，工作树服务就绪后代理切换到修复内容；刷新原浏览器标签页即可验收，
域名、端口、路径、查询参数和 hash 路由保持不变，浏览器登录存储和 Cookie 继续使用原来源。
代理转发 HTTP 和 HMR WebSocket，切换时关闭旧 HMR 连接。托管开发服务设置 `BROWSER=none`，
抑制 Vite 等开发工具的自动开窗行为（包括项目的 `server.open: true`），避免暴露内部随机端口。
预览不会自动打开新窗口；如启动脚本显式执行系统打开浏览器命令，应移除该步骤。
点击「返回原项目」、完成验收或修复服务异常退出时，代理恢复仍在运行的原项目，无需重启原项目。
未配置固定入口时，预览使用独立端口，可手动点击「打开预览」。
启动状态和最近 20000 字符日志可在弹窗查看，120 秒未就绪则停止并报告错误。
同一仓库最多一个预览，切换任务自动停止旧服务；不同仓库可以同时预览。
关闭弹窗保留预览，再次打开可复用；停止预览、确认/驳回验收和删除任务时清理工作树服务进程组。
项目列表的「停止项目」同时停止固定入口、原项目及该仓库所有预览。运行期间修改仓库、固定端口、
启动命令或解除绑定会返回 409，先停止项目后再修改配置。
后端关闭、开发模式重载时清理全部代理和开发服务；异常退出时守护进程通过输入管道断开终止子进程。
因此后端重启后需重新点击「启动项目」，浏览器地址仍保持原配置。

接口：`GET/POST/DELETE /api/agents/{agent_id}/preview`，分别读取状态、启动和停止。
项目开发入口：`GET/POST/DELETE /api/projects/{project_id}/development`，分别读取状态、启动和停止项目。
项目配置增加可选 `preview_port` 字段（1–65535），预览状态包含 `mode`（fixed/direct）、
对外 `port` 和实际服务 `internal_port`。
仅当前待验收且工作树仍与已验证提交一致的任务可以启动。旧数据库启动时自动补齐配置列，
旧调用方保存项目时未传预览字段会保留已有设置。
当前面向本机单后端进程使用，固定入口绑定 IPv4/IPv6 loopback；远程访问和 HTTPS 需额外部署配置。

验证：`python3 -m pytest tests/test_preview_gateway.py tests/test_preview_manager.py tests/test_projects.py tests/test_agent_tasks.py tests/test_server_reload.py -q`。

## 云效缺陷同步

可在前端「通用配置 → 云效 MCP 配置」保存连接信息，保存后立即生效。
配置保存在 `backend/data/yunxiao_config.json`，文件权限为仅当前用户可读写；Token 不回显，留空保存保留已有 Token。
该文件存在时优先使用其中的云效配置，否则继续读取 `backend/.env`。设置 `YUNXIAO_API_BASE=https://openapi-rdc.aliyuncs.com`、
`YUNXIAO_ACCESS_TOKEN`、实际 `YUNXIAO_ORG_ID`，以及 `YUNXIAO_PROJECT_ID`。
项目 ID 填 `*` 时遍历该组织下所有可访问项目；指定实际 ID 时只同步该项目。
同步读取需要组织用户、项目、工作项和工作流的读取权限；验收后的状态更新还需要工作项读写权限。
`viewIdentifier` 是视图 ID，不是项目 ID。

`POST /api/bugs/sync` 分页查询 Token 所属用户负责的 Bug，包括历史已关闭记录。
完整读取成功后才在一个事务中更新本地数据库，返回 `total/created/updated/unchanged`。
重复同步保持本地修复状态、仓库路径、分支和 Agent 分配不变。
`source_status` 和 `source_project` 分别保留云效原始状态与项目名称。
新导入的已修复、已关闭、暂不修复记录标记为 `resolved`，其他记录标记为 `pending`。
本地录入时间是入库时间，非云效工作项创建时间。

验证：`python3 -m pytest tests/test_bug_sync.py -q`。

确认云效来源的 Bug 已修复且代码成功合并后，系统通过该项目工作项类型的工作流解析唯一的“处理中”
状态 ID，并更新云效 Bug。自动验证通过、等待确认、反馈仍未修复、合并失败或手动创建的 Bug 都不会触发更新。
本地修复状态仍为“已解决”，`source_status` 仅在云效结果确认成功后更新为“处理中”。
若云效缺少写权限、工作流没有唯一目标状态、请求超时或结果无法确认，代码交付保持成功，
验收记录的 `yunxiao_sync` 保存 pending/succeeded/failed 状态，界面显示原因并提供重试。
`POST /api/agents/{agent_id}/yunxiao-status-sync` 只重试状态同步，不重复合并或清理；已同步成功直接返回已有结果。
后端重启保留待同步记录，可在验收详情重试。本次新增规则不追溯修改已完成的历史任务。
官方接口：[更新工作项](https://help.aliyun.com/zh/yunxiao/developer-reference/updateworkitem)、
[工作流状态列表](https://help.aliyun.com/zh/yunxiao/developer-reference/getworkitemworkflow-get-a-list-of-the-status-of-the-work-item)。

验证：`python3 -m pytest tests/test_yunxiao_status_sync.py tests/test_agent_tasks.py tests/test_bug_sync.py -q`。

## 修复前补充信息

点击 Bug 标题，在详情的富文本框中填写补充说明，直接粘贴截图到光标位置，也可将图片拖入编辑框，然后选择「保存补充信息」或「保存并开始修复」。支持加粗、斜体、列表、图片预览和删除，以及撤销与重做，无需单独上传文件。说明最多 12000 字，截图最多 4 张，单张不超过 5MB，支持 PNG、JPEG、WebP、GIF。

方案反馈和验收反馈共用同一富文本输入组件，反馈文字最多 4000 字，截图限制与补充说明一致。网页粘贴内容只保留支持的文字格式和实际携带的图片内容；网页远程图片需复制图片或截图后粘贴。

补充内容存放在独立的 `bug_supplements` 表中，不写回云效，也不会被同步覆盖。`GET/PUT /api/bugs/{bug_id}/supplement` 读取或保存说明、截图、富文本 `document` 和版本号 `revision`；版本不匹配时返回 409，避免其他页面的修改被覆盖。`document` 使用编辑器 JSON，图片节点仅保存 `image_index` 引用；后端校验文档并按图文顺序生成 Agent 的文字输入。旧版仅包含文字和图片的请求、已保存内容继续兼容。

任务入队时固定补充资料，首次读取正文后将正文和原图加入 `task_history.result.input_snapshot`。截图按内容哈希保存在数据库同目录的 `feedback_images/` 中，续跑和方案确认读取同一份输入。任务执行中保存的补充信息留待下一轮使用；失败任务的补充内容发生变化时，「重新执行」会新建任务并重新分析根因。

后端启动时会自动创建新表，并为已有补充信息表添加 `document` 列，已有 Bug、状态和修复记录保持不变。备份数据库时应一并备份 `feedback_images/`，历史任务需要这些图片文件；从当前补充资料删除图片不会删除历史快照引用的文件。

验证：`python3 -m pytest tests/test_rich_input.py tests/test_bug_supplements.py tests/test_agent_retry.py tests/test_feedback_images.py -q`。

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

后端要求 Python 3.11+。首次运行请创建独立环境，并始终使用该环境启动：

```bash
python3.11 -m venv venv
venv/bin/python -m pip install -r requirements.txt
venv/bin/python -m src.main
```

已有 `venv` 若使用 Python 3.9/3.10，请先移走后重建。启动入口会检查版本，
避免进入预览等功能后才出现 `asyncio.timeout` 不存在的错误。

或使用 uvicorn：

```bash
mkdir -p .worktrees
venv/bin/python -m uvicorn src.main:app --reload --reload-dir src --reload-exclude .worktrees --host 0.0.0.0 --port 8000
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
