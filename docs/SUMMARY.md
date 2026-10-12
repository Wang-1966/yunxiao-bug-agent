# 云效 Bug 修复 Agent - 文档总结

## 📋 核心理解

### Agent 的本质

**Agent 不是去"调用"Claude Code 或 Grok Build，而是自己实现相同的能力！**

```
Claude Code = Claude Opus 5 + 工具调用
Grok Build  = Grok 4.7 + 工具调用
本项目     = 可配置的 LLM + 工具集 + Web UI + 多并发
```

**Agent 拥有和这些工具完全相同的能力：**
- 读取和分析代码
- 理解 bug 描述
- 设计修复方案
- 编写和修改代码
- 运行测试验证
- Git 操作（commit、push）

**额外的增强：**
- ✅ Web UI 可视化操作
- ✅ 多 Agent 并发修复
- ✅ 可配置 LLM 后端（Claude/Grok/GPT）
- ✅ 云效自动集成
- ✅ 成本优化（不同阶段用不同模型）

## 🎯 产品定位

一个基于 Web UI 的自主 bug 修复系统，支持多 agent 并发工作，用户通过可视化界面管理修复流程。

## 🏗️ 技术架构

```
┌─────────────────────────────────────┐
│   Web UI (React + Ant Design)      │
│   - Dashboard                       │
│   - Bug 列表                        │
│   - Agent 监控                      │
│   - LLM 配置                        │
└─────────────┬───────────────────────┘
              │ HTTP / WebSocket
┌─────────────▼───────────────────────┐
│   API 网关 (FastAPI)                │
└─────────────┬───────────────────────┘
              │
┌─────────────▼───────────────────────┐
│   多 Agent 调度器                   │
│   - 任务队列                        │
│   - 并发控制（3-5 个 agent）        │
│   - 负载均衡                        │
└─────────────┬───────────────────────┘
              │
    ┌─────────┼─────────┐
    │         │         │
┌───▼───┐ ┌──▼───┐ ┌──▼───┐
│Agent-1│ │Agent-2│ │Agent-3│
└───┬───┘ └──────┘ └──────┘
    │
    │ 每个 Agent = LangGraph + 可配置的 LLM + 工具集
    │
┌───▼──────────────────────────────┐
│  可配置的 LLM 后端                │
│  ┌────────────────────────────┐  │
│  │ • Claude Opus 5  (官方)    │  │
│  │ • Grok 4.7  (中转站)       │  │
│  │ • GPT-4  (OpenAI)          │  │
│  │ • 混合模式（分阶段配置）   │  │
│  └────────────────────────────┘  │
└───┬──────────────────────────────┘
    │
┌───▼──────────────────────────────┐
│  工具集                           │
│  - 云效 MCP                       │
│  - 代码分析                       │
│  - 代码修改                       │
│  - 测试执行                       │
│  - Git 操作                       │
└───────────────────────────────────┘
```

## 🔧 核心功能

### 1. Web UI 界面
- Dashboard（bug 统计、agent 状态）
- Bug 列表页（筛选、搜索、批量操作）
- Bug 详情页（修复历史、代码变更）
- Agent 监控页（实时状态、进度、日志）
- **LLM 配置页（模型选择、API 配置、成本估算）**
- 决策确认弹窗（方案选择、最终确认）

### 2. 多 Agent 并发系统
- 同时运行 3-5 个 agent
- 每个 agent 独立的 Git Worktree（避免冲突）
- 任务队列和自动调度
- 按优先级分配任务

### 3. LLM 配置管理 ⭐ 核心特性
- **支持多种 LLM**：
  - Claude Opus 5（理解能力最强）
  - Grok 4.7（性价比高，通过中转站）
  - GPT-4（备选）

- **灵活配置**：
  - 全局默认模型
  - 不同阶段用不同模型（成本优化）
  - API Key 安全存储
  - 中转站 URL 配置

- **成本优化**：
  ```yaml
  推荐配置（混合模式）：
    分析根因: Claude Opus 5   # $0.10
    设计方案: Grok 4.7        # $0.04
    实施修复: Grok 4.7        # $0.05
    验证修复: Claude Sonnet 4 # $0.01
  
  预估成本:
    - Claude Opus 5 全程: ~$0.50/bug
    - 混合配置: ~$0.20/bug (节省 60%)
    - Grok 4.7 全程: ~$0.15/bug
  ```

### 4. 完整的修复流程
```
获取 Bug → 分析根因 → (需要日志?) → 设计方案 
  → (用户确认) → 实施修复 → 验证 
  → (用户确认) → 推送代码 + 更新云效
```

### 5. 云效集成
- 自动获取 bug 列表
- 更新 bug 状态
- 添加修复说明

### 6. 实时通信
- WebSocket 推送 agent 状态更新
- 决策请求实时通知
- 日志流式输出

## 💻 技术栈

### 前端
- React 18 + TypeScript
- Ant Design 5（UI 组件库）
- Zustand（状态管理）
- TanStack Query（数据请求）
- WebSocket（实时通信）
- Vite（构建工具）

### 后端
- FastAPI（Web 框架）
- LangGraph（Agent 状态机）
- **多 LLM 支持**（Claude/Grok/GPT）
- 云效 MCP（Bug 平台集成）
- SQLite（数据持久化）
- asyncio.Queue（任务队列）

## 📊 关键设计

### LLM 架构（核心）

**统一的 LLM 客户端接口：**
```python
class LLMClient(ABC):
    async def ainvoke(messages, temperature, max_tokens) -> str
    async def test_connection() -> bool
    def get_model_info() -> dict
```

**具体实现：**
- `ClaudeLLMClient` - 官方 Anthropic API
- `GrokLLMClient` - 中转站 API（OpenAI 兼容格式）
- `OpenAILLMClient` - OpenAI API

**工厂模式：**
```python
# 根据阶段自动选择模型
llm = LLMFactory.create(stage="analyze")  # → Claude Opus 5
llm = LLMFactory.create(stage="fix")      # → Grok 4.7
```

### Agent 工作流（LangGraph）

```python
获取 bug → 分析根因 → (置信度低?) → 添加日志 → 等待用户
                ↓
            (置信度高)
                ↓
          设计方案 → 等待用户确认
                ↓
          实施修复 → 验证修复
                ↓
          (测试通过?) → 等待最终确认 → 完成
                ↓
          (测试失败) → 重新分析
```

### 多 Agent 隔离（Git Worktree）

```
项目根目录/
├── main/              # 主工作区
├── .worktrees/
│   ├── agent-1/       # Agent-1 独立工作区
│   ├── agent-2/       # Agent-2 独立工作区
│   └── agent-3/       # Agent-3 独立工作区
```

## 🎨 UI 设计示例

### LLM 配置页面
```
┌─────────────────────────────────────┐
│  LLM 配置                            │
├─────────────────────────────────────┤
│  基础配置                            │
│  默认模型: [Grok 4.7 ▼]             │
│                                      │
│  高级配置 - 不同阶段使用不同模型    │
│  分析根因: [Claude Opus 5 ▼]        │
│  设计方案: [Grok 4.7 ▼]             │
│  实施修复: [Grok 4.7 ▼]             │
│  验证修复: [Claude Sonnet 4 ▼]      │
│                                      │
│  API 配置                            │
│  Claude API Key: [sk-ant-***] [测试]│
│  Grok 中转站 URL: [https://...]     │
│  Grok API Key: [***] [测试]          │
│                                      │
│  成本预估                            │
│  单个 Bug 预估: $0.20 (节省 60%)    │
│  - 分析: $0.10 (Claude Opus 5)      │
│  - 方案: $0.04 (Grok 4.7)           │
│  - 修复: $0.05 (Grok 4.7)           │
│  - 验证: $0.01 (Claude Sonnet 4)    │
│                                      │
│  [保存配置] [测试连接] [重置]        │
└─────────────────────────────────────┘
```

## 📅 开发计划（4 周）

### Week 1: 基础架构 + LLM 层
- 前后端项目搭建
- **LLM 客户端实现（Claude + Grok + OpenAI）**
- **LLM 配置管理和 API**
- 数据库设计
- WebSocket 通信

### Week 2: 单 Agent 功能
- LangGraph 状态机
- 云效 MCP 集成
- **单 Agent 使用可配置的 LLM**
- 基础 UI 页面
- **LLM 配置页面**

### Week 3: 多 Agent 系统
- Agent 调度器
- Git Worktree 隔离
- 任务队列和负载均衡
- Agent 监控页面

### Week 4: 优化和测试
- UI/UX 优化
- **不同 LLM 性能和成本对比测试**
- 测试用例编写
- 文档完善
- V1.0 发布

## ❓ FAQ

### Q: Agent 和 Claude Code 有什么区别？
**A:** 
- **相同**：都是 LLM + 工具集
- **不同**：
  - Agent 有 Web UI，Claude Code 是 CLI
  - Agent 支持多并发，Claude Code 单线程
  - Agent 可配置不同 LLM，Claude Code 只用 Claude
  - Agent 集成云效，自动管理 bug

### Q: 为什么不直接调用 Grok Build？
**A:** 
- Grok Build 本质是 Grok API + 工具调用
- Agent 自己实现了相同能力
- 更灵活（可配置不同 LLM）
- 更强大（多并发、Web UI）

### Q: Grok 中转站怎么配置？
**A:**
1. 在 LLM 配置页面填入中转站 URL
2. 填入中转站提供的 API Key
3. 点击"测试连接"验证
4. 保存配置

### Q: 如何优化成本？
**A:**
1. 使用混合模式（不同阶段用不同模型）
2. 分析用 Claude（理解能力强）
3. 修复用 Grok（性价比高）
4. 验证用 Sonnet（快速便宜）
5. 预估可节省 60% 成本

### Q: Agent 如何保证质量？
**A:**
- 不猜测根因（置信度低时添加日志）
- 提供多方案对比
- 全面测试验证
- 关键决策点需要你确认

### Q: 多 Agent 会冲突吗？
**A:**
- 不会，每个 Agent 在独立的 Git Worktree
- 完全隔离，互不影响
- 修复完成后合并回主分支

## ✅ 请确认

1. **架构设计**是否正确理解？
   - Agent 自己实现修复能力，不调用外部工具
   - 支持配置不同 LLM（Claude/Grok/GPT）
   - 多 Agent 并发工作

2. **LLM 配置方案**是否满足需求？
   - 支持 Grok 中转站
   - 可以不同阶段用不同模型
   - 有成本估算

3. **Web UI 方案**是否符合预期？
   - 可视化操作
   - 实时查看 agent 状态
   - 弹窗确认决策

4. **有没有遗漏或需要调整的地方**？

确认无误后，我开始创建代码！
