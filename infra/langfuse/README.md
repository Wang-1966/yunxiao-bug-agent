# 自托管 Langfuse

采用 Langfuse 官方 Docker Compose 架构（Web、Worker、PostgreSQL、ClickHouse、Redis、MinIO），独立于业务服务运行。仅在本机开放 3002（Web）与 19090（对象存储），避免占用项目的 3001。

首次在仓库根目录执行：

```sh
backend/venv/bin/python infra/langfuse/setup.py
docker compose -f infra/langfuse/compose.yaml up -d
```

启动 Docker Desktop 后运行上述命令。初始化脚本生成随机密钥、管理员密码和项目，凭据写入被 Git 忽略的 `infra/langfuse/.env`，并更新 `backend/.env` 的 Langfuse 配置，保留其他配置。重复运行保留平台凭据；已有自定义 Langfuse 服务时直接配置后端环境变量，不运行本机初始化脚本。

安装 `backend/requirements.txt` 并重启后端后启用监控。访问 http://localhost:3002 ，使用 `.env` 中 `LANGFUSE_ADMIN_EMAIL`、`LANGFUSE_ADMIN_PASSWORD` 登录。初始化项目为 **Bug Repair Agent**。

## 调用链

每个修复 Agent 对应一个 trace 和 session；分析、修复、只读讨论分别为 agent observation，下面记录 generation（每次模型请求）和 tool（每次工具调用）。Bug ID、任务 ID、运行 ID 与阶段保存在 metadata。重试和阶段切换沿用 Agent 的 trace ID。Pi 的请求、压缩请求、工具事件由桥接层送入官方 Langfuse Python SDK，SDK 异步批量上报；Python 的截图识别请求记录为 `vision` 并关联同一修复任务；未携带任务标识的连接测试等辅助请求单独记录为 `auxiliary-llm`。

输入、输出经过凭据脱敏，图片载荷省略，长字符串最多 20000 字符，列表最多保留最后 100 项；因此监控记录不是完整上下文备份。token 使用提供商通过 SDK 返回的数据，不自行估算；Pi SDK 未收到用量时可能返回零，需要与提供商账单核对。费用依赖 Langfuse 是否识别模型及其价格配置，不能把未知费用当作零。工具错误、未正常结束的请求和阶段失败标为 ERROR。

本地 SQLite 日志与证据文件继续用于任务展示、恢复及验收。Langfuse 不决定修复、审批或交付；关闭监控或服务不可达不会中断任务。后台 SDK 导出失败仍可能输出诊断日志。进程正常退出时 flush，强制终止可能丢失尚未上报的一小批事件。

## 配置与维护

后端读取以下变量（也支持进程环境变量）：

```dotenv
LANGFUSE_ENABLED=true
LANGFUSE_BASE_URL=http://localhost:3002
LANGFUSE_PUBLIC_KEY=<项目公钥>
LANGFUSE_SECRET_KEY=<项目私钥>
LANGFUSE_ENVIRONMENT=local
```

设置 `LANGFUSE_ENABLED=false` 并重启可停止上报。

```sh
docker compose -f infra/langfuse/compose.yaml ps
docker compose -f infra/langfuse/compose.yaml logs --tail=100 web worker
docker compose -f infra/langfuse/compose.yaml stop
```

数据放在 Docker named volumes。停止服务不会删除数据；不要使用 `down -v`，除非明确要删除监控数据。备份应同时包含 `.env` 和各数据卷。基础环境适用于本机开发；公开部署需要域名、TLS、资源规划和备份策略。MinIO 与官方示例使用相同的 Chainguard 发行镜像。
