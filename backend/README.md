# 云效 Bug 修复 Agent - 后端

执行路径只走 Pi：模型请求、工具循环、压缩和会话在 `pi/` 的 Node 桥接里完成。Python 负责调度、权限、证据、验证、合并与云效同步。LangGraph 仅编排阶段，不再跑 native 工具循环。总览见仓库 [README](../README.md)，内核边界见 [Pi 执行内核](docs/pi-conversation.md)。

## 修复分支与 Git 记录

新任务的隔离分支使用 `agentfix/<Bug短ID>-<短标题>-<运行短ID>`，例如
`agentfix/c790db24-讲义历史样式异常-46bea39aadd6`。标题清理为合法分支字符并限制为 24 个字符，
Bug ID 取前 8 个字符，运行 ID 取前 12 个字符；完整 Bug 标题仍保存在任务中。
修复提交使用 `fix: <Bug标题>`，正文记录完整 Bug ID。确认修复后，合并记录显示
`Merge branch 'agentfix/...' into <目标分支>`，实际合并对象仍是已验证的完整提交 hash。
新规则仅应用于后续任务，不重命名已有分支或重写 Git 历史。清理根据任务记录和 Git 工作树注册信息进行，
同时识别已有 `bug-agent/` 分支，不依赖由分支名称拼接工作树路径。

## 修复工作树依赖

创建任务与失败续跑时，若源仓库已安装根目录 `node_modules`，且双方依赖清单、锁文件和工作区配置一致，
系统会准备独立的依赖副本。macOS 使用写时复制；工作树的依赖与缓存写入不会修改源仓库。
已有工作树依赖不会被覆盖，不复制私有仓库凭据；依赖配置不一致、包含外部符号链接或未被 Git 忽略时，
仍需独立安装。根目录之外的依赖安装也由项目自身命令负责。

复现命令若因明确的缺包或浏览器未安装而失败，会被标记为环境问题，不作为业务 Bug 已复现的证据。

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
对已入库但不再出现在负责人查询结果中的云效 Bug（例如转交他人），逐条读取详情刷新来源字段，不导入其他人的新 Bug。
`total` 包含负责人查询结果和补查的已入库记录；补查失败则整次同步不写入，并返回具体 Bug ID 和失败原因。
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

点击 Bug 标题，在详情的富文本框中填写补充说明，直接粘贴截图到光标位置，也可将图片拖入编辑框，然后选择「保存补充信息」或「保存并开始修复」。支持加粗、斜体、列表、图片预览和删除，以及撤销与重做，无需单独上传文件。补充说明不设固定字数上限，截图最多 4 张，单张不超过 5MB，支持 PNG、JPEG、WebP、GIF。

方案反馈和验收反馈共用同一富文本输入组件，反馈文字最多 4000 字，截图限制与补充说明一致。网页粘贴内容只保留支持的文字格式和实际携带的图片内容；网页远程图片需复制图片或截图后粘贴。

补充内容存放在独立的 `bug_supplements` 表中，不写回云效，也不会被同步覆盖。`GET/PUT /api/bugs/{bug_id}/supplement` 读取或保存说明、截图、富文本 `document` 和版本号 `revision`；版本不匹配时返回 409，避免其他页面的修改被覆盖。`document` 使用编辑器 JSON，图片节点仅保存 `image_index` 引用；后端校验文档并按图文顺序生成 Agent 的文字输入。旧版仅包含文字和图片的请求、已保存内容继续兼容。

任务入队时固定补充资料，首次读取正文后将正文和原图加入 `task_history.result.input_snapshot`。截图按内容哈希保存在数据库同目录的 `feedback_images/` 中，续跑和方案确认读取同一份输入。任务执行中保存的补充信息留待下一轮使用；失败任务的补充内容发生变化时，「重新执行」会新建任务并重新分析根因。

后端启动时会自动创建新表，并为已有补充信息表添加 `document` 列，已有 Bug、状态和修复记录保持不变。备份数据库时应一并备份 `feedback_images/`，历史任务需要这些图片文件；从当前补充资料删除图片不会删除历史快照引用的文件。

验证：`python3 -m pytest tests/test_rich_input.py tests/test_bug_supplements.py tests/test_agent_retry.py tests/test_feedback_images.py -q`。

## 技术栈与结构

- FastAPI、LangGraph（仅阶段图）、Pi Coding Agent 1.5.0、OpenAI SDK、SQLite、loguru
- 可选 Langfuse SDK，见 [自托管说明](../infra/langfuse/README.md)

```
backend/
├── src/main.py config.py
├── src/api/            # bugs、agents、llm_config、projects、settings、websocket
├── src/agent/          # 阶段图 graph.py、节点 nodes.py
├── src/core/           # 调度、Pi 宿主、验证合并、预览、云效、证据
├── src/llm/            # OpenAI 兼容客户端
├── pi/                 # Node 桥接、模型窗口默认表
├── docs/               # 内核与上下文说明
├── tests/
└── requirements.txt
```

`GET /api/settings/agent-runtime` 只报告 Pi 是否已安装，不能切换内核。

## 启动

需要 Python 3.11+ 与 Node.js ≥ 22.19。推荐在仓库根目录用 `./scripts/start.sh`。仅启动后端：

```bash
python3.11 -m venv venv
venv/bin/python -m pip install -r requirements.txt
npm ci --prefix pi
venv/bin/python -m src.main
```

已有 `venv` 若低于 3.11，移走后重建。热重载必须排除工作树，否则依赖安装会重启后端并取消任务。`python -m src.main` 会自动创建并排除 `WORKTREES_DIR`。直接使用 uvicorn 时自行加 `--reload-exclude`。

模型在 Web UI「模型与通用配置」中保存到 `data/llm_config.json`。分析/讨论走 `analyze`，修复走 `fix`。上下文窗口由提供商返回值、`pi/model-context-defaults.json` 或 Pi 目录解析，用户无需填写。

API：http://localhost:8000/docs 。健康检查：http://localhost:8000/health 。

## 阶段流程

```
fetch_bug → prepare_repository → analyze_root_cause → design_proposal
        →（人工确认方案）→ implement_fix → verify_fix（构建 + 复现测试）
```

分析在隔离工作树里写复现测试并提交方案；确认后 Pi 续接会话改代码。验证通过由系统 `commit-tree`，合并仍走人工验收。详见 [修复交付闭环](../docs/AGENT_DELIVERY.md)。

## 开发

上下文压缩由 Pi 管理，策略版本 v2。证据回查、指标与报告见 [运行说明](docs/context-management-operations.md)。历史设计稿见 [设计方案](docs/context-management-design.md)。

```bash
venv/bin/python -m src.core.context_report
venv/bin/python -m src.core.context_report --agent agent-task-xxx
venv/bin/python -m pytest tests/ -q
```

Pi 相关回归：`venv/bin/python -m pytest tests/test_pi_runtime.py tests/test_context_management.py tests/test_conversation.py tests/test_agent_harness.py -q`。
