# 云效 Bug 修复 Agent

本机 Web 系统，从云效拉取 Bug，用可配置的 OpenAI 兼容模型在隔离工作树里分析、修复并验证。多个 Agent 可并发工作；方案确认、逐项验收和合并仍由人决定。

## 当前架构

LangGraph 只负责阶段编排。编码、工具循环、上下文压缩和会话续跑全部交给 Pi（`@earendil-works/pi-coding-agent`）。Python 宿主管权限、受控搜索、复现验证、证据、Git 交付和人工审批。旧 native 工具循环与内核切换已删除。

```
浏览器  React + Ant Design  :3001
   │  HTTP / WebSocket
FastAPI  :8000
   ├── 调度器：队列、并发（默认 3）、暂停 / 续跑
   ├── 阶段图：获取 Bug → 工作树 → 分析 → 方案确认 → 修复 → 验证
   ├── Pi 子进程：模型请求、原生读写/编辑/查找/命令、压缩、JSONL 会话
   └── Python 宿主：路径权限、受控 grep、证据、复现与构建、commit-tree、云效同步
          │
SQLite · agent_sessions/*.pi.jsonl · agent_context/
可选：自托管 Langfuse :3002（调用监控，不参与审批）
```

分析与只读讨论使用 `analyze` 阶段模型；修复使用 `fix` 阶段模型。界面上的 propose / verify 配置保留，当前不会单独发起这两段模型循环。

## 核心能力

- 云效缺陷同步，确认合并后回写工作项状态
- 多 Agent 并发，每个任务独立 Git worktree 与 `agentfix/...` 分支
- 修复工作台：对话、方案选择、活动状态、停止与继续修复
- 先失败复现测试，再改业务代码；验证通过后由系统 `commit-tree`，模型不能自行提交或合并
- 逐项验收、工作树预览（可走原浏览器端口）和合并冲突保留
- 上下文窗口按提供商 / 项目默认表 / Pi 目录自动解析，压缩由 Pi 管理
- 可选 Langfuse：模型输入输出、工具、耗时与 token

## 快速开始

需要 **Python 3.11+**、**Node.js ≥ 22.19**、Git。前端开发服务为 **http://localhost:3001**。

```bash
# 可选：复制环境变量模板（云效也可之后在界面填写）
cp backend/.env.example backend/.env

./scripts/start.sh
# 指定 Python：BACKEND_PYTHON=/path/to/python3.11 ./scripts/start.sh
```

停止：`./scripts/stop.sh`。

手动启动：

```bash
cd backend
python3.11 -m venv venv
venv/bin/python -m pip install -r requirements.txt
# 首次或 Pi 依赖缺失时：
npm ci --prefix pi
venv/bin/python -m src.main          # http://localhost:8000

cd frontend
npm install
npm run dev                          # http://localhost:3001
```

打开界面后：

1. **模型与通用配置**：添加 OpenAI 兼容提供商，拉取模型列表，测试连接并保存。不必填写上下文窗口。
2. **项目与仓库**：绑定源码仓库、目标分支、验证命令；需要验收预览时再填启动命令。
3. **云效 MCP 配置**：组织、项目、Token；项目 ID 填 `*` 同步组织下可访问项目。
4. **Bug 悬赏榜**同步缺陷，补充说明后开始修复；在 **Agent 工位** 或 `/agents/:id` 工作台跟进。

可选监控见 [Langfuse 运维说明](infra/langfuse/README.md)。健康检查：http://localhost:8000/health 。API 文档：http://localhost:8000/docs 。

## 使用要点

- **待我处理**：方案待选、待回答、待验收和异常，按 Bug 优先级与等待时间排序。
- 新分析会为验收要求写下覆盖说明或人工检查步骤。系统命令结果与 Agent 解释分开展示；构建成功不会被当成全部业务条目通过。
- 逐项「通过」后可确认合并；「未通过」进入下一轮。文字、截图与条目选择存在浏览器 IndexedDB。
- 中断后点「继续修复」或发送明确短指令，Pi 恢复已保存会话；普通闲聊不会改代码或批准合并。
- 合并冲突时保留原修复提交，可通过「仍未修复」在目标分支最新代码上重跑。

## 文档

| 文档 | 内容 |
|------|------|
| [快速开始](QUICKSTART.md) | 安装、配置、常见问题 |
| [Pi 执行内核](backend/docs/pi-conversation.md) | 职责边界、会话、恢复、智能确认 |
| [上下文管理](backend/docs/context-management-operations.md) | 压缩、证据、指标与报告 |
| [模型窗口默认配置](backend/docs/model-context-defaults.md) | 窗口来源与维护 |
| [修复交付闭环](docs/AGENT_DELIVERY.md) | 验证、commit-tree、审查、合并 |
| [后端说明](backend/README.md) | 工作树、预览、云效同步、补充信息 |
| [Langfuse](infra/langfuse/README.md) | 自托管监控 |
| [提示词](backend/src/prompts/README.md) | 模板与回答风格 |

早期 PRD / TDD 等设计稿仍保留，正文可能落后于实现，以本页和上表为准。

## 技术栈

**前端：** React 18、TypeScript、Ant Design 5、Zustand、React Router、Axios、Vite（`npm run dev` 使用 3001 端口）。

**后端：** FastAPI、LangGraph（仅阶段图）、Pi Coding Agent 1.5.0、OpenAI SDK、SQLite、Langfuse SDK（可选）。

**工具：** Pi 原生文件与命令、macOS `sandbox-exec`、项目受控 grep、Git worktree / commit-tree、云效 OpenAPI。

## 项目结构

```
yunxiao-bug-agent/
├── frontend/                 # React 界面
├── backend/
│   ├── src/api/              # HTTP / WebSocket
│   ├── src/agent/            # 阶段图与节点
│   ├── src/core/             # 调度、Pi 宿主、验证、合并、预览
│   ├── src/llm/              # OpenAI 兼容客户端
│   ├── pi/                   # Pi 桥接与模型窗口配置
│   └── docs/                 # 执行内核与上下文说明
├── infra/langfuse/           # 自托管监控
├── docs/                     # 交付闭环与历史设计稿
└── scripts/start.sh
```

## 许可证

MIT License
