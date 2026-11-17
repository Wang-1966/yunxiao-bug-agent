# 快速开始

## 环境

- Python 3.11+
- Node.js ≥ 22.19（Pi 执行内核）
- Git
- 可选：Docker Desktop（自托管 Langfuse）

## 一键启动

```bash
cp backend/.env.example backend/.env   # 可先不填，云效也可在界面配置
./scripts/start.sh
# 指定解释器：BACKEND_PYTHON=/path/to/python3.11 ./scripts/start.sh
```

启动脚本会安装后端依赖、准备 Pi（`backend/pi` 下 `npm ci`）、安装前端依赖，并拉起服务。

- 前端：http://localhost:3001
- 后端：http://localhost:8000
- API 文档：http://localhost:8000/docs
- 健康检查：http://localhost:8000/health

停止：`./scripts/stop.sh`。日志：`tail -f backend/backend.log`。

## 手动启动

```bash
cd backend
python3.11 -m venv venv
venv/bin/python -m pip install -r requirements.txt
npm ci --prefix pi
venv/bin/python -m src.main
```

已有 `venv` 若低于 Python 3.11，移走后重建。开发热重载必须排除工作树，避免安装依赖时重启并取消任务。`python -m src.main` 会自动排除 `WORKTREES_DIR`。

另开终端：

```bash
cd frontend
npm install
npm run dev
```

`package.json` 将开发端口固定为 3001。`vite.config.ts` 里的 3000 仅在不用该脚本时生效；CORS 同时允许两者。

## 首次配置

模型、云效和仓库都在 Web UI 中配置，不使用 `CLAUDE_RELAY_*` / `GROK_RELAY_*` 环境变量。

1. 打开 http://localhost:3001 → **模型与通用配置**
2. 添加 OpenAI 兼容提供商（Base URL、API Key），拉取模型列表并测试连接
3. 可选：为分析（`analyze`）和修复（`fix`）选择不同模型。不必填写上下文窗口
4. **项目与仓库**绑定源码路径、目标分支和验证命令
5. **通用配置 → 云效 MCP 配置**填写组织、项目与 Token；也可写在 `backend/.env`，界面保存的 `backend/data/yunxiao_config.json` 优先

`backend/.env` 常用项：

```env
API_HOST=0.0.0.0
API_PORT=8000
DATABASE_URL=sqlite+aiosqlite:///./data/yunxiao_agent.db
MAX_CONCURRENT_AGENTS=3
AGENT_TIMEOUT=1800
WORKTREES_DIR=.worktrees
YUNXIAO_API_BASE=https://openapi-rdc.aliyuncs.com
YUNXIAO_ACCESS_TOKEN=
YUNXIAO_ORG_ID=
YUNXIAO_PROJECT_ID=*
YUNXIAO_SYNC_INTERVAL_SECONDS=300
```

可选 Langfuse 见 [自托管说明](infra/langfuse/README.md)。设置 `LANGFUSE_ENABLED=false` 或不配密钥即不上报。

## 使用

1. **Bug 悬赏榜**同步云效，或手工建 Bug
2. 在详情补充说明 / 截图，开始修复
3. **Agent 工位实况**看并发状态；点「进入修复」打开工作台对话
4. 确认方案后进入修复与验证；验收通过再合并
5. 失败或停止后点「继续修复」，或发送「继续」「重试」等明确短指令

并发默认最多 3 个 Agent，各自独立 worktree。

## 常见问题

**后端启动失败 / 数据库错误**  
确认 Python 3.11+，并存在 `backend/data/`。首次启动会建库。

**前端连不上后端**  
访问 http://localhost:8000/health 应返回 `{"status":"healthy"}`。

**提示 Pi 未安装或 Node 版本不足**  
安装 Node.js 22.19+，在 `backend/pi` 执行 `npm ci`。

**Git / 工作树错误**  
目标必须是 Git 仓库；检查 `backend/.worktrees/` 权限。热重载不要监视工作树。

**LLM 调用失败**  
在「模型与通用配置」检查 Base URL、Key 和模型名，先点测试连接。未知模型不会回退成 128K 窗口，界面会提示未收录。

**WebSocket 失败**  
确认后端已启动。开发代理把 `/ws` 转到 `ws://localhost:8000`。

## 更多说明

- [项目总览](README.md)
- [Pi 执行内核](backend/docs/pi-conversation.md)
- [后端：工作树 / 预览 / 云效](backend/README.md)
- [修复交付闭环](docs/AGENT_DELIVERY.md)
