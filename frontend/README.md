# 云效 Bug 修复 Agent - 前端

React 18 + TypeScript + Vite + Ant Design 5。开发地址默认 **http://localhost:3001**（`npm run dev`）。后端 API 与 WebSocket 经 Vite 代理到 `http://localhost:8000`。

## 命令

```bash
npm install
npm run dev      # http://localhost:3001
npm run build
npm run preview
npm run lint
```

仓库根目录 `./scripts/start.sh` 会同时拉起前后端。

## 页面

| 路径 | 说明 |
|------|------|
| `/` | 仪表盘：Bug 统计与 Agent 概览 |
| `/bugs`、`/bugs/:bugId` | 缺陷列表与详情、补充信息、启动修复 |
| `/agents` | 工位实况：并发状态、待我处理 |
| `/agents/:agentId` | 修复工作台：对话、方案、验收入口 |
| `/projects` | 仓库、验证命令、开发/预览入口 |
| `/llm-config` | 提供商、阶段模型、云效与提示词风格 |
| `/confirmation-settings` | 方案确认策略（always / smart / never） |

主题：暖调工坊、赛博、街机，写在本地 `themeStore`。

## 技术栈

React 18、TypeScript、Vite、Ant Design 5、Zustand、React Router 6、Axios、Tiptap（富文本）。请求走 Axios，实时状态走 WebSocket，没有 TanStack Query。
