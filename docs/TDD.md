# 云效 Bug 修复 Agent - 技术设计文档 (TDD) v2.0

## 一、技术架构

### 1.1 整体架构

```
┌─────────────────────────────────────────────────────────────┐
│                         前端层 (React)                       │
│                         Port: 3000                          │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  │
│  │Dashboard │  │Bug 列表  │  │Agent监控 │  │Bug详情   │  │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘  │
└────────────────────────┬────────────────────────────────────┘
                         │ HTTP / WebSocket
┌────────────────────────┴────────────────────────────────────┐
│                    API 网关层 (FastAPI)                      │
│                         Port: 8000                          │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  │
│  │REST API  │  │WebSocket │  │认证中间件│  │CORS      │  │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘  │
└────────────────────────┬────────────────────────────────────┘
                         │
┌────────────────────────┴────────────────────────────────────┐
│                    业务逻辑层                                │
│  ┌─────────────────────────────────────────────────────┐   │
│  │          多 Agent 调度器 (AgentOrchestrator)        │   │
│  │  - 任务队列管理                                      │   │
│  │  - Agent 生命周期管理                                │   │
│  │  - 并发控制 (最多 N 个 agent)                        │   │
│  │  - 负载均衡                                          │   │
│  └─────────────────────────────────────────────────────┘   │
│                         │                                    │
│  ┌──────────┬──────────┼──────────┬──────────┐             │
│  │          │          │          │          │             │
│  ▼          ▼          ▼          ▼          ▼             │
│  Agent-1   Agent-2   Agent-3   Agent-4   Agent-5           │
│  (LangGraph) (LangGraph) (LangGraph) ...                   │
│                                                              │
│  每个 Agent:                                                 │
│  ┌─────┐  ┌─────┐  ┌─────┐  ┌─────┐  ┌─────┐            │
│  │获取 │→ │分析 │→ │方案 │→ │修复 │→ │验证 │            │
│  └─────┘  └─────┘  └─────┘  └─────┘  └─────┘            │
└────────────────────────┬────────────────────────────────────┘
                         │
┌────────────────────────┴────────────────────────────────────┐
│                      工具层                                  │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  │
│  │云效 MCP  │  │代码分析  │  │测试执行  │  │Git操作   │  │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘  │
└────────────────────────┬────────────────────────────────────┘
                         │
┌────────────────────────┴────────────────────────────────────┐
│                     持久化层                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐     │
│  │ SQLite       │  │ LangGraph    │  │ File System  │     │
│  │ (业务数据)   │  │ Checkpoints  │  │ (日志、代码) │     │
│  └──────────────┘  └──────────────┘  └──────────────┘     │
└─────────────────────────────────────────────────────────────┘
```

### 1.2 技术栈选型

| 层级 | 技术选型 | 理由 |
|------|----------|------|
| **前端** |
| 框架 | React 18 + TypeScript | 生态成熟，类型安全 |
| UI 组件库 | Ant Design 5 | 企业级 UI，组件丰富 |
| 状态管理 | Zustand | 轻量，易用 |
| 数据请求 | TanStack Query v5 | 强大的缓存和同步 |
| 实时通信 | WebSocket (原生 API) | 双向通信，实时推送 |
| 路由 | React Router v6 | 标准路由方案 |
| 构建工具 | Vite | 快速的开发体验 |
| **后端** |
| Web 框架 | FastAPI | 高性能，原生异步，自动文档 |
| Agent 框架 | LangGraph | 状态管理、检查点、工具集成 |
| **LLM（可配置）** | **支持多种模型** | **灵活选择** |
| - Claude Opus 5 | Anthropic API | 代码理解能力最强 |
| - Grok 4.7 | 中转站 API (OpenAI 兼容) | 性价比高 |
| - GPT-4 | OpenAI API | 备选方案 |
| - 混合模式 | 不同阶段用不同模型 | 成本优化 |
| 云效集成 | MCP (Model Context Protocol) | 官方推荐，标准化工具协议 |
| 数据库 | SQLite | 轻量、可靠、无需额外服务 |
| 任务队列 | asyncio.Queue | 轻量级异步队列 |
| WebSocket | FastAPI WebSocket | 内置支持 |
| **工具** |
| 代码分析 | Tree-sitter + AST | 准确的语法分析 |
| 测试执行 | subprocess | 兼容各种测试框架 |
| Git 操作 | GitPython | Python Git 封装 |
| 配置管理 | Pydantic Settings | 类型安全配置 |

### 1.3 多 Agent 隔离方案

为避免多个 agent 同时修改代码造成冲突，使用 **Git Worktree** 机制：

```
项目根目录/
├── .git/
├── main/                    # 主工作区
│   ├── src/
│   └── ...
├── .worktrees/              # Agent 工作区
│   ├── agent-1/             # Agent-1 的独立工作区
│   │   ├── src/
│   │   └── ...
│   ├── agent-2/             # Agent-2 的独立工作区
│   │   ├── src/
│   │   └── ...
│   └── agent-3/
```

**优势：**
- 完全隔离，互不影响
- 共享 .git 对象，节省空间
- 各自独立的分支和暂存区
- 修复完成后合并回主分支

## 二、LLM 架构设计（核心）

### 2.1 为什么需要可配置的 LLM 后端

**Agent 的本质**：Agent = LLM + 工具集 + 状态管理

- **Claude Code** = Claude Opus 5 + 工具调用（读文件、写代码、运行命令）
- **Grok Build** = Grok 4.7 + 工具调用（同样的能力）
- **本项目 Agent** = 可配置的 LLM + 工具集 + Web UI + 多并发

**Agent 不需要"调用"Claude Code 或 Grok Build，而是自己实现相同的能力，但提供更好的用户体验和并发能力。**

### 2.2 统一的 LLM 客户端接口

```python
# backend/src/llm/base.py
from abc import ABC, abstractmethod
from typing import List, Dict, Optional

class Message(TypedDict):
    role: Literal["user", "assistant", "system"]
    content: str

class LLMClient(ABC):
    """统一的 LLM 客户端接口"""
    
    @abstractmethod
    async def ainvoke(
        self, 
        messages: List[Message],
        temperature: float = 0.0,
        max_tokens: int = 8000
    ) -> str:
        """调用 LLM 生成响应"""
        pass
    
    @abstractmethod
    async def test_connection(self) -> bool:
        """测试 API 连接"""
        pass
    
    @abstractmethod
    def get_model_info(self) -> Dict[str, any]:
        """获取模型信息（名称、成本等）"""
        pass
```

### 2.3 各 LLM 实现

#### Claude 客户端
```python
# backend/src/llm/claude_client.py
from anthropic import AsyncAnthropic

class ClaudeLLMClient(LLMClient):
    """Claude API 客户端"""
    
    def __init__(self, api_key: str, model: str = "claude-opus-5-5"):
        self.client = AsyncAnthropic(api_key=api_key)
        self.model = model
        
    async def ainvoke(
        self, 
        messages: List[Message],
        temperature: float = 0.0,
        max_tokens: int = 8000
    ) -> str:
        response = await self.client.messages.create(
            model=self.model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens
        )
        return response.content[0].text
    
    async def test_connection(self) -> bool:
        try:
            await self.ainvoke([{"role": "user", "content": "test"}])
            return True
        except Exception as e:
            logger.error(f"Claude API test failed: {e}")
            return False
    
    def get_model_info(self) -> Dict[str, any]:
        return {
            "name": self.model,
            "provider": "Anthropic",
            "input_cost_per_1m": 15.0,   # $15 per 1M tokens
            "output_cost_per_1m": 75.0,  # $75 per 1M tokens
            "context_window": 200000
        }
```

#### Grok 客户端（通过中转站）
```python
# backend/src/llm/grok_client.py
from openai import AsyncOpenAI

class GrokLLMClient(LLMClient):
    """Grok API 客户端（通过中转站，兼容 OpenAI 格式）"""
    
    def __init__(
        self, 
        api_key: str, 
        base_url: str,
        model: str = "grok-4.7"
    ):
        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url  # 中转站 URL
        )
        self.model = model
        
    async def ainvoke(
        self, 
        messages: List[Message],
        temperature: float = 0.0,
        max_tokens: int = 8000
    ) -> str:
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens
        )
        return response.choices[0].message.content
    
    async def test_connection(self) -> bool:
        try:
            await self.ainvoke([{"role": "user", "content": "test"}])
            return True
        except Exception as e:
            logger.error(f"Grok API test failed: {e}")
            return False
    
    def get_model_info(self) -> Dict[str, any]:
        return {
            "name": self.model,
            "provider": "xAI (via relay)",
            "input_cost_per_1m": 5.0,    # 假设价格
            "output_cost_per_1m": 15.0,
            "context_window": 128000
        }
```

#### OpenAI 客户端
```python
# backend/src/llm/openai_client.py
from openai import AsyncOpenAI

class OpenAILLMClient(LLMClient):
    """OpenAI API 客户端"""
    
    def __init__(self, api_key: str, model: str = "gpt-4"):
        self.client = AsyncOpenAI(api_key=api_key)
        self.model = model
        
    async def ainvoke(
        self, 
        messages: List[Message],
        temperature: float = 0.0,
        max_tokens: int = 8000
    ) -> str:
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens
        )
        return response.choices[0].message.content
    
    async def test_connection(self) -> bool:
        try:
            await self.ainvoke([{"role": "user", "content": "test"}])
            return True
        except Exception as e:
            logger.error(f"OpenAI API test failed: {e}")
            return False
    
    def get_model_info(self) -> Dict[str, any]:
        return {
            "name": self.model,
            "provider": "OpenAI",
            "input_cost_per_1m": 30.0,
            "output_cost_per_1m": 60.0,
            "context_window": 128000
        }
```

### 2.4 LLM 配置管理

```python
# backend/src/config/llm_config.py
from pydantic import BaseModel
from typing import Optional, Dict, Literal

class StageModelConfig(BaseModel):
    """不同阶段的模型配置"""
    analyze: str = "claude-opus-5-5"      # 分析根因
    propose: str = "grok-4.7"             # 设计方案
    fix: str = "grok-4.7"                 # 实施修复
    verify: str = "claude-sonnet-4"       # 验证修复

class APIKeyConfig(BaseModel):
    """API 密钥配置"""
    anthropic: Optional[str] = None
    grok: Optional[str] = None
    openai: Optional[str] = None

class RelayConfig(BaseModel):
    """中转站配置"""
    grok_base_url: str = "https://your-relay.com/v1"
    grok_model: str = "grok-4.7"

class LLMConfig(BaseModel):
    """LLM 总配置"""
    # 默认模型
    default_model: Literal["claude-opus-5-5", "grok-4.7", "gpt-4"] = "claude-opus-5-5"
    
    # 是否启用不同阶段用不同模型
    use_stage_models: bool = False
    stage_models: Optional[StageModelConfig] = None
    
    # API 配置
    api_keys: APIKeyConfig
    relay_config: Optional[RelayConfig] = None
    
    # 其他配置
    temperature: float = 0.0
    max_tokens: int = 8000
    timeout: int = 120  # 秒

# 全局配置实例
_llm_config: Optional[LLMConfig] = None

def get_llm_config() -> LLMConfig:
    """获取 LLM 配置"""
    global _llm_config
    if _llm_config is None:
        # 从数据库或配置文件加载
        _llm_config = load_llm_config_from_db()
    return _llm_config

def update_llm_config(config: LLMConfig):
    """更新 LLM 配置"""
    global _llm_config
    _llm_config = config
    save_llm_config_to_db(config)
```

### 2.5 LLM 工厂

```python
# backend/src/llm/factory.py
from typing import Optional

class LLMFactory:
    """LLM 客户端工厂"""
    
    @staticmethod
    def create(
        model_name: Optional[str] = None,
        stage: Optional[str] = None
    ) -> LLMClient:
        """
        创建 LLM 客户端
        
        Args:
            model_name: 指定模型名称，如果为 None 则从配置读取
            stage: 指定阶段（analyze/propose/fix/verify），用于获取该阶段的模型
        
        Returns:
            LLM 客户端实例
        """
        config = get_llm_config()
        
        # 确定使用哪个模型
        if model_name:
            # 显式指定
            target_model = model_name
        elif stage and config.use_stage_models and config.stage_models:
            # 根据阶段选择
            target_model = getattr(config.stage_models, stage, config.default_model)
        else:
            # 使用默认模型
            target_model = config.default_model
        
        # 创建对应的客户端
        if target_model.startswith("claude"):
            if not config.api_keys.anthropic:
                raise ValueError("Claude API key not configured")
            return ClaudeLLMClient(
                api_key=config.api_keys.anthropic,
                model=target_model
            )
        
        elif target_model.startswith("grok"):
            if not config.api_keys.grok or not config.relay_config:
                raise ValueError("Grok API key or relay config not configured")
            return GrokLLMClient(
                api_key=config.api_keys.grok,
                base_url=config.relay_config.grok_base_url,
                model=target_model
            )
        
        elif target_model.startswith("gpt"):
            if not config.api_keys.openai:
                raise ValueError("OpenAI API key not configured")
            return OpenAILLMClient(
                api_key=config.api_keys.openai,
                model=target_model
            )
        
        else:
            raise ValueError(f"Unsupported model: {target_model}")
    
    @staticmethod
    async def test_all_connections() -> Dict[str, bool]:
        """测试所有已配置的 API 连接"""
        config = get_llm_config()
        results = {}
        
        if config.api_keys.anthropic:
            try:
                client = ClaudeLLMClient(config.api_keys.anthropic)
                results["claude"] = await client.test_connection()
            except:
                results["claude"] = False
        
        if config.api_keys.grok and config.relay_config:
            try:
                client = GrokLLMClient(
                    config.api_keys.grok,
                    config.relay_config.grok_base_url
                )
                results["grok"] = await client.test_connection()
            except:
                results["grok"] = False
        
        if config.api_keys.openai:
            try:
                client = OpenAILLMClient(config.api_keys.openai)
                results["openai"] = await client.test_connection()
            except:
                results["openai"] = False
        
        return results
```

### 2.6 在 Agent 中使用

```python
# agent/nodes.py
async def analyze_root_cause_node(state: BugFixState) -> BugFixState:
    """分析根因节点"""
    
    state["stage"] = "analyzing"
    await notify_stage_update(state)
    
    # 获取这个阶段配置的 LLM（会自动根据配置选择模型）
    llm = LLMFactory.create(stage="analyze")
    
    # 构建 prompt
    messages = [
        {
            "role": "system",
            "content": ANALYZE_SYSTEM_PROMPT
        },
        {
            "role": "user",
            "content": f"""
分析以下 Bug 的根本原因：

Bug 信息：
{json.dumps(state["bug_info"], ensure_ascii=False, indent=2)}

相关代码：
{format_code_context(state["code_context"])}

要求：
1. 确定根本原因（不是表象）
2. 评估置信度（high/medium/low）
3. 如果置信度不是 high，建议添加日志的位置

返回 JSON 格式。
"""
        }
    ]
    
    # 调用 LLM
    response = await llm.ainvoke(messages, temperature=0.0)
    analysis = parse_json(response)
    
    # 记录使用的模型
    model_info = llm.get_model_info()
    await send_log(
        state["bug_id"], 
        "info", 
        f"使用 {model_info['name']} 进行分析"
    )
    
    # 更新状态
    state["root_cause"] = analysis["root_cause"]
    state["confidence"] = analysis["confidence"]
    state["need_logging"] = analysis["need_logging"]
    state["log_locations"] = analysis.get("log_locations", [])
    
    return state

async def implement_fix_node(state: BugFixState) -> BugFixState:
    """实施修复节点"""
    
    state["stage"] = "fixing"
    await notify_stage_update(state)
    
    # 修复阶段可能用不同的模型（比如 Grok，便宜一些）
    llm = LLMFactory.create(stage="fix")
    
    messages = [
        {
            "role": "system",
            "content": FIX_SYSTEM_PROMPT
        },
        {
            "role": "user",
            "content": f"""
根据以下方案实施修复：

根因：{state["root_cause"]}
方案：{json.dumps(state["selected_proposal"], ensure_ascii=False, indent=2)}

相关代码：
{format_code_context(state["code_context"])}

要求：
1. 生成完整的代码修改
2. 添加单元测试
3. 更新相关文档

返回 JSON 格式，包含所有文件的修改内容。
"""
        }
    ]
    
    response = await llm.ainvoke(messages, temperature=0.0)
    fix_result = parse_json(response)
    
    # 应用代码修改
    modified_files = []
    for file_change in fix_result["changes"]:
        await apply_code_change(file_change)
        modified_files.append(file_change["file_path"])
    
    state["modified_files"] = modified_files
    
    # 创建 git commit
    git_operator = get_git_operator()
    commit_hash = git_operator.commit_changes(
        message=f"fix: {state['bug_info']['title']}\n\n{state['root_cause']}",
        files=modified_files
    )
    state["git_commit_hash"] = commit_hash
    
    return state
```

## 三、前端架构设计

### 2.1 目录结构

```
frontend/
├── src/
│   ├── api/                 # API 调用
│   │   ├── bugs.ts
│   │   ├── agents.ts
│   │   └── yunxiao.ts
│   ├── components/          # 通用组件
│   │   ├── AgentCard/
│   │   ├── BugTable/
│   │   ├── ProgressBar/
│   │   └── DecisionModal/
│   ├── pages/              # 页面组件
│   │   ├── Dashboard/
│   │   ├── BugList/
│   │   ├── BugDetail/
│   │   ├── AgentMonitor/
│   │   └── Settings/
│   ├── stores/             # 状态管理
│   │   ├── bugStore.ts
│   │   ├── agentStore.ts
│   │   └── wsStore.ts
│   ├── hooks/              # 自定义 Hooks
│   │   ├── useWebSocket.ts
│   │   ├── useBugList.ts
│   │   └── useAgentStatus.ts
│   ├── types/              # TypeScript 类型
│   │   ├── bug.ts
│   │   ├── agent.ts
│   │   └── api.ts
│   ├── utils/              # 工具函数
│   │   ├── formatter.ts
│   │   └── constants.ts
│   ├── App.tsx
│   └── main.tsx
├── package.json
├── tsconfig.json
└── vite.config.ts
```

### 2.2 核心数据类型

```typescript
// types/bug.ts
export enum BugPriority {
  CRITICAL = "严重",
  IMPORTANT = "重要",
  NORMAL = "普通"
}

export enum BugStatus {
  PENDING = "待修复",
  IN_PROGRESS = "修复中",
  FIXED = "已修复",
  CLOSED = "已关闭"
}

export interface Bug {
  id: string;
  title: string;
  description: string;
  priority: BugPriority;
  status: BugStatus;
  assignee?: string;
  createdAt: string;
  updatedAt: string;
  labels?: string[];
  yunxiaoUrl?: string;
}

// types/agent.ts
export enum AgentStage {
  IDLE = "idle",
  FETCHING = "fetching",
  ANALYZING = "analyzing",
  LOGGING = "logging",
  PROPOSING = "proposing",
  FIXING = "fixing",
  VERIFYING = "verifying",
  COMPLETED = "completed",
  FAILED = "failed"
}

export interface Agent {
  id: string;
  name: string;
  status: "idle" | "busy" | "paused" | "error";
  currentBugId?: string;
  stage: AgentStage;
  progress: number;  // 0-100
  logs: AgentLog[];
  createdAt: string;
  startedAt?: string;
  workspaceDir?: string;
}

export interface AgentLog {
  timestamp: string;
  level: "info" | "warning" | "error";
  message: string;
}

// types/decision.ts
export interface DecisionRequest {
  agentId: string;
  bugId: string;
  type: "logging_confirm" | "proposal_select" | "final_confirm";
  data: any;
  timestamp: string;
}

export interface LoggingConfirmData {
  rootCause: string;
  confidence: "high" | "medium" | "low";
  logLocations: string[];
  options: {
    id: string;
    label: string;
    description: string;
    recommended: boolean;
  }[];
}

export interface ProposalSelectData {
  proposals: {
    id: number;
    title: string;
    description: string;
    pros: string[];
    cons: string[];
    complexity: "low" | "medium" | "high";
    recommended: boolean;
  }[];
}

export interface FinalConfirmData {
  summary: {
    rootCause: string;
    solution: string;
    modifiedFiles: string[];
    gitCommit: string;
  };
  verification: {
    testsPassed: boolean;
    totalTests: number;
    passedTests: number;
    affectedFiles: string[];
  };
  nextActions: {
    pushCode: boolean;
    updateYunxiao: boolean;
    createPR: boolean;
  };
}
```

### 2.3 WebSocket 实时通信

```typescript
// hooks/useWebSocket.ts
import { useEffect, useRef, useState } from 'react';
import { useAgentStore } from '@/stores/agentStore';

export function useWebSocket() {
  const ws = useRef<WebSocket | null>(null);
  const [connected, setConnected] = useState(false);
  const { updateAgent, addLog } = useAgentStore();

  useEffect(() => {
    // 连接 WebSocket
    ws.current = new WebSocket('ws://localhost:8000/ws');

    ws.current.onopen = () => {
      console.log('WebSocket connected');
      setConnected(true);
    };

    ws.current.onmessage = (event) => {
      const message = JSON.parse(event.data);
      
      switch (message.type) {
        case 'agent_update':
          // 更新 agent 状态
          updateAgent(message.data);
          break;
        
        case 'agent_log':
          // 添加日志
          addLog(message.agentId, message.log);
          break;
        
        case 'decision_request':
          // 需要用户决策
          showDecisionModal(message.data);
          break;
        
        case 'bug_fixed':
          // Bug 修复完成
          notification.success({
            message: 'Bug 修复完成',
            description: `Bug #${message.bugId} 已成功修复`
          });
          break;
      }
    };

    ws.current.onclose = () => {
      console.log('WebSocket disconnected');
      setConnected(false);
      // 自动重连
      setTimeout(() => {
        // reconnect logic
      }, 3000);
    };

    return () => {
      ws.current?.close();
    };
  }, []);

  const sendMessage = (message: any) => {
    if (ws.current?.readyState === WebSocket.OPEN) {
      ws.current.send(JSON.stringify(message));
    }
  };

  return { connected, sendMessage };
}
```

### 2.4 核心组件设计

#### Dashboard 页面
```typescript
// pages/Dashboard/index.tsx
import { useBugStats, useAgentList } from '@/hooks';
import { AgentCard, BugStatsCard, QuickActions } from '@/components';

export default function Dashboard() {
  const { data: stats } = useBugStats();
  const { data: agents } = useAgentList();

  return (
    <div className="dashboard">
      {/* Bug 统计卡片 */}
      <div className="stats-grid">
        <BugStatsCard label="总计" count={stats?.total} />
        <BugStatsCard label="严重" count={stats?.critical} color="red" />
        <BugStatsCard label="重要" count={stats?.important} color="orange" />
        <BugStatsCard label="普通" count={stats?.normal} color="blue" />
      </div>

      {/* 活跃 Agent */}
      <div className="agents-section">
        <h2>活跃 Agent ({agents?.active}/{agents?.total})</h2>
        <div className="agents-grid">
          {agents?.list.map(agent => (
            <AgentCard key={agent.id} agent={agent} />
          ))}
        </div>
      </div>

      {/* 快速操作 */}
      <QuickActions />
    </div>
  );
}
```

#### Agent 卡片组件
```typescript
// components/AgentCard/index.tsx
import { Progress, Button, Tag } from 'antd';
import { Agent, AgentStage } from '@/types';

interface AgentCardProps {
  agent: Agent;
}

const STAGE_LABELS = {
  [AgentStage.IDLE]: '空闲',
  [AgentStage.FETCHING]: '获取 Bug',
  [AgentStage.ANALYZING]: '分析根因',
  [AgentStage.LOGGING]: '添加日志',
  [AgentStage.PROPOSING]: '设计方案',
  [AgentStage.FIXING]: '实施修复',
  [AgentStage.VERIFYING]: '验证修复',
  [AgentStage.COMPLETED]: '已完成',
  [AgentStage.FAILED]: '失败'
};

export function AgentCard({ agent }: AgentCardProps) {
  const stageLabel = STAGE_LABELS[agent.stage];
  
  return (
    <div className="agent-card">
      <div className="agent-header">
        <h3>{agent.name}</h3>
        <Tag color={agent.status === 'busy' ? 'green' : 'default'}>
          {agent.status}
        </Tag>
      </div>

      {agent.currentBugId && (
        <div className="agent-work">
          <p>正在修复 Bug #{agent.currentBugId}</p>
          <p className="stage">阶段: {stageLabel}</p>
          <Progress percent={agent.progress} status="active" />
        </div>
      )}

      <div className="agent-actions">
        <Button size="small" disabled={agent.status !== 'busy'}>
          暂停
        </Button>
        <Button size="small" danger disabled={agent.status === 'idle'}>
          停止
        </Button>
        <Button size="small" type="link">
          查看详情
        </Button>
      </div>
    </div>
  );
}
```

#### 决策确认弹窗
```typescript
// components/DecisionModal/index.tsx
import { Modal, Radio, Input, Button } from 'antd';
import { DecisionRequest } from '@/types';

interface DecisionModalProps {
  request: DecisionRequest;
  onConfirm: (decision: any) => void;
  onCancel: () => void;
}

export function DecisionModal({ request, onConfirm, onCancel }: DecisionModalProps) {
  const [selectedOption, setSelectedOption] = useState<string>();
  const [note, setNote] = useState<string>('');

  const renderContent = () => {
    switch (request.type) {
      case 'logging_confirm':
        return <LoggingConfirmContent data={request.data} />;
      case 'proposal_select':
        return <ProposalSelectContent data={request.data} />;
      case 'final_confirm':
        return <FinalConfirmContent data={request.data} />;
    }
  };

  return (
    <Modal
      title="需要你的确认"
      open={true}
      onOk={() => onConfirm({ selectedOption, note })}
      onCancel={onCancel}
      width={800}
    >
      {renderContent()}
      
      <div style={{ marginTop: 16 }}>
        <Input.TextArea
          placeholder="备注（可选）"
          value={note}
          onChange={(e) => setNote(e.target.value)}
          rows={2}
        />
      </div>
    </Modal>
  );
}
```

## 三、后端架构设计

### 3.1 目录结构

```
backend/
├── src/
│   ├── api/                    # API 路由
│   │   ├── bugs.py
│   │   ├── agents.py
│   │   └── websocket.py
│   ├── core/                   # 核心业务逻辑
│   │   ├── orchestrator.py     # Agent 调度器
│   │   ├── agent_manager.py    # Agent 生命周期管理
│   │   └── task_queue.py       # 任务队列
│   ├── agent/                  # Agent 实现
│   │   ├── bug_fix_agent.py    # Bug 修复 Agent
│   │   ├── graph.py            # LangGraph 状态图
│   │   ├── nodes.py            # 各个节点实现
│   │   └── state.py            # Agent 状态定义
│   ├── tools/                  # 工具集成
│   │   ├── yunxiao/            # 云效 MCP
│   │   ├── code_analyzer/      # 代码分析
│   │   ├── test_runner/        # 测试执行
│   │   └── git_operator/       # Git 操作
│   ├── models/                 # 数据模型
│   │   ├── bug.py
│   │   ├── agent.py
│   │   └── decision.py
│   ├── db/                     # 数据库
│   │   ├── database.py
│   │   └── repositories/
│   ├── services/               # 服务层
│   │   ├── bug_service.py
│   │   ├── agent_service.py
│   │   └── notification_service.py
│   ├── config.py               # 配置
│   ├── main.py                 # 应用入口
│   └── utils/                  # 工具函数
├── tests/
├── requirements.txt
└── pyproject.toml
```

### 3.2 多 Agent 调度器

```python
# core/orchestrator.py
import asyncio
from typing import Dict, Optional
from dataclasses import dataclass
from enum import Enum

@dataclass
class Task:
    id: str
    bug_id: str
    priority: int
    created_at: datetime
    assigned_agent_id: Optional[str] = None

class AgentOrchestrator:
    """多 Agent 调度器"""
    
    def __init__(self, max_concurrent_agents: int = 3):
        self.max_concurrent_agents = max_concurrent_agents
        self.agents: Dict[str, BugFixAgent] = {}
        self.task_queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
        self.running = False
        
    async def start(self):
        """启动调度器"""
        self.running = True
        
        # 初始化 agent pool
        for i in range(self.max_concurrent_agents):
            agent = BugFixAgent(
                id=f"agent-{i+1}",
                name=f"Agent-{i+1}",
                workspace_dir=f".worktrees/agent-{i+1}"
            )
            self.agents[agent.id] = agent
        
        # 启动调度循环
        asyncio.create_task(self._schedule_loop())
        
    async def _schedule_loop(self):
        """调度循环"""
        while self.running:
            # 查找空闲 agent
            idle_agent = self._find_idle_agent()
            
            if idle_agent and not self.task_queue.empty():
                # 从队列获取任务
                priority, task = await self.task_queue.get()
                
                # 分配给 agent
                await self._assign_task(idle_agent, task)
            
            # 等待一段时间再检查
            await asyncio.sleep(1)
    
    def _find_idle_agent(self) -> Optional[BugFixAgent]:
        """查找空闲的 agent"""
        for agent in self.agents.values():
            if agent.status == AgentStatus.IDLE:
                return agent
        return None
    
    async def _assign_task(self, agent: BugFixAgent, task: Task):
        """分配任务给 agent"""
        task.assigned_agent_id = agent.id
        
        # 在后台运行 agent
        asyncio.create_task(self._run_agent(agent, task))
    
    async def _run_agent(self, agent: BugFixAgent, task: Task):
        """运行 agent 修复 bug"""
        try:
            # 设置 agent 状态
            agent.status = AgentStatus.BUSY
            agent.current_bug_id = task.bug_id
            
            # 通知前端
            await self._notify_agent_update(agent)
            
            # 运行修复流程
            result = await agent.fix_bug(task.bug_id)
            
            # 修复完成
            if result.success:
                await self._handle_fix_success(agent, task, result)
            else:
                await self._handle_fix_failure(agent, task, result)
                
        except Exception as e:
            logger.error(f"Agent {agent.id} error: {e}")
            await self._handle_agent_error(agent, task, e)
        
        finally:
            # 重置 agent 状态
            agent.status = AgentStatus.IDLE
            agent.current_bug_id = None
            await self._notify_agent_update(agent)
    
    async def submit_task(self, bug_id: str, priority: int = 0):
        """提交修复任务"""
        task = Task(
            id=generate_id(),
            bug_id=bug_id,
            priority=priority,
            created_at=datetime.now()
        )
        
        # 加入优先级队列（优先级高的先执行）
        await self.task_queue.put((-priority, task))
        
        return task.id
    
    async def pause_agent(self, agent_id: str):
        """暂停 agent"""
        agent = self.agents.get(agent_id)
        if agent:
            agent.pause()
            await self._notify_agent_update(agent)
    
    async def stop_agent(self, agent_id: str):
        """停止 agent"""
        agent = self.agents.get(agent_id)
        if agent:
            agent.stop()
            await self._notify_agent_update(agent)
    
    async def _notify_agent_update(self, agent: BugFixAgent):
        """通知前端 agent 状态更新"""
        await websocket_manager.broadcast({
            "type": "agent_update",
            "data": agent.to_dict()
        })
    
    def get_agent_status(self, agent_id: str) -> Optional[dict]:
        """获取 agent 状态"""
        agent = self.agents.get(agent_id)
        return agent.to_dict() if agent else None
    
    def get_all_agents(self) -> list[dict]:
        """获取所有 agent 状态"""
        return [agent.to_dict() for agent in self.agents.values()]
```

### 3.3 Agent 状态机（LangGraph）

```python
# agent/state.py
from typing import TypedDict, Literal, Optional

class BugFixState(TypedDict):
    # Bug 信息
    bug_id: str
    bug_info: dict
    
    # 当前阶段
    stage: Literal[
        "idle", "fetching", "analyzing", "logging",
        "proposing", "fixing", "verifying", "completed", "failed"
    ]
    
    # 分析结果
    root_cause: Optional[str]
    confidence: Literal["high", "medium", "low"]
    need_logging: bool
    log_locations: list[str]
    
    # 方案
    proposals: list[dict]
    selected_proposal: Optional[dict]
    
    # 修复
    modified_files: list[str]
    git_commit_hash: Optional[str]
    
    # 验证
    test_results: dict
    verification_passed: bool
    
    # 用户交互
    waiting_for_user: bool
    user_decision: Optional[dict]
    
    # 错误
    error: Optional[str]
    retry_count: int

# agent/graph.py
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.sqlite import SqliteSaver

def create_bug_fix_graph(agent_id: str) -> StateGraph:
    """创建 bug 修复工作流图"""
    
    workflow = StateGraph(BugFixState)
    
    # 添加节点
    workflow.add_node("fetch_bug", fetch_bug_node)
    workflow.add_node("analyze", analyze_root_cause_node)
    workflow.add_node("add_logging", add_logging_node)
    workflow.add_node("wait_user", wait_user_decision_node)
    workflow.add_node("design_proposal", design_proposal_node)
    workflow.add_node("implement_fix", implement_fix_node)
    workflow.add_node("verify_fix", verify_fix_node)
    
    # 设置入口
    workflow.set_entry_point("fetch_bug")
    
    # 添加边
    workflow.add_edge("fetch_bug", "analyze")
    
    # 条件边：分析后决定是否需要日志
    workflow.add_conditional_edges(
        "analyze",
        should_add_logging,
        {
            "logging": "add_logging",
            "proposal": "design_proposal"
        }
    )
    
    workflow.add_edge("add_logging", "wait_user")
    
    # 用户提供日志后继续分析
    workflow.add_conditional_edges(
        "wait_user",
        check_user_decision_type,
        {
            "continue_analyze": "analyze",
            "select_proposal": "implement_fix",
            "final_confirm": END
        }
    )
    
    workflow.add_edge("design_proposal", "wait_user")
    workflow.add_edge("implement_fix", "verify_fix")
    
    # 验证后决定
    workflow.add_conditional_edges(
        "verify_fix",
        check_verification,
        {
            "success": "wait_user",  # 等待最终确认
            "retry": "analyze",
            "fail": END
        }
    )
    
    # 编译图，使用独立的 checkpointer
    checkpointer = SqliteSaver(f"checkpoints_{agent_id}.db")
    app = workflow.compile(checkpointer=checkpointer)
    
    return app
```

### 3.4 节点实现示例

```python
# agent/nodes.py
import asyncio
from langchain_anthropic import ChatAnthropic

async def analyze_root_cause_node(state: BugFixState) -> BugFixState:
    """分析根因节点"""
    
    # 更新阶段
    state["stage"] = "analyzing"
    await notify_stage_update(state)
    
    # 获取 bug 信息
    bug_info = state["bug_info"]
    
    # 搜索相关代码
    code_analyzer = get_code_analyzer()
    related_files = await code_analyzer.find_related_files(
        keywords=extract_keywords(bug_info["description"])
    )
    
    # 使用 Claude 分析
    llm = ChatAnthropic(model="claude-opus-5", temperature=0)
    
    prompt = f"""
    分析以下 Bug 的根本原因：
    
    Bug 信息：
    {bug_info}
    
    相关代码文件：
    {format_code_context(related_files)}
    
    要求：
    1. 确定根本原因（不是表象）
    2. 评估置信度（high/medium/low）
    3. 如果置信度不是 high，建议添加日志的位置
    
    返回 JSON 格式。
    """
    
    result = await llm.ainvoke(prompt)
    analysis = parse_json(result.content)
    
    # 更新状态
    state["root_cause"] = analysis["root_cause"]
    state["confidence"] = analysis["confidence"]
    state["need_logging"] = analysis["need_logging"]
    state["log_locations"] = analysis.get("log_locations", [])
    
    # 发送日志
    await send_log(state["bug_id"], "info", f"根因分析完成：{analysis['root_cause']}")
    
    return state

async def wait_user_decision_node(state: BugFixState) -> BugFixState:
    """等待用户决策节点"""
    
    state["waiting_for_user"] = True
    
    # 根据当前阶段发送不同的决策请求
    if state["stage"] == "analyzing" and state["need_logging"]:
        # 需要用户确认是否添加日志
        decision_request = {
            "type": "logging_confirm",
            "data": {
                "root_cause": state["root_cause"],
                "confidence": state["confidence"],
                "log_locations": state["log_locations"]
            }
        }
    elif state["stage"] == "proposing":
        # 需要用户选择方案
        decision_request = {
            "type": "proposal_select",
            "data": {
                "proposals": state["proposals"]
            }
        }
    elif state["stage"] == "verifying":
        # 需要用户最终确认
        decision_request = {
            "type": "final_confirm",
            "data": {
                "summary": {
                    "root_cause": state["root_cause"],
                    "solution": state["selected_proposal"]["title"],
                    "modified_files": state["modified_files"],
                    "git_commit": state["git_commit_hash"]
                },
                "verification": state["test_results"]
            }
        }
    
    # 发送决策请求到前端
    await websocket_manager.broadcast({
        "type": "decision_request",
        "agentId": state.get("agent_id"),
        "bugId": state["bug_id"],
        "data": decision_request,
        "timestamp": datetime.now().isoformat()
    })
    
    # 等待用户决策（通过事件机制）
    decision_event = asyncio.Event()
    pending_decisions[state["bug_id"]] = decision_event
    
    await decision_event.wait()
    
    # 获取用户决策
    user_decision = pending_decisions_data.pop(state["bug_id"])
    state["user_decision"] = user_decision
    state["waiting_for_user"] = False
    
    return state
```

### 3.5 FastAPI 路由

```python
# api/agents.py
from fastapi import APIRouter, HTTPException
from typing import List

router = APIRouter(prefix="/api/agents", tags=["agents"])

@router.get("/", response_model=List[AgentResponse])
async def get_agents():
    """获取所有 agent 状态"""
    orchestrator = get_orchestrator()
    agents = orchestrator.get_all_agents()
    return agents

@router.post("/{agent_id}/pause")
async def pause_agent(agent_id: str):
    """暂停 agent"""
    orchestrator = get_orchestrator()
    await orchestrator.pause_agent(agent_id)
    return {"message": "Agent paused"}

@router.post("/{agent_id}/stop")
async def stop_agent(agent_id: str):
    """停止 agent"""
    orchestrator = get_orchestrator()
    await orchestrator.stop_agent(agent_id)
    return {"message": "Agent stopped"}

@router.post("/submit-task")
async def submit_task(request: SubmitTaskRequest):
    """提交修复任务"""
    orchestrator = get_orchestrator()
    task_id = await orchestrator.submit_task(
        bug_id=request.bug_id,
        priority=request.priority
    )
    return {"taskId": task_id}

# api/websocket.py
from fastapi import WebSocket, WebSocketDisconnect
from typing import Set

class WebSocketManager:
    def __init__(self):
        self.active_connections: Set[WebSocket] = set()
    
    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.add(websocket)
    
    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)
    
    async def broadcast(self, message: dict):
        """广播消息到所有连接的客户端"""
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except:
                pass

websocket_manager = WebSocketManager()

@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket_manager.connect(websocket)
    try:
        while True:
            # 接收前端消息
            data = await websocket.receive_json()
            
            # 处理用户决策
            if data["type"] == "user_decision":
                await handle_user_decision(data)
            
    except WebSocketDisconnect:
        websocket_manager.disconnect(websocket)
```

## 四、API 路由设计

### 4.1 LLM 配置 API

```python
# api/llm_config.py
from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/llm-config", tags=["llm-config"])

@router.get("/", response_model=LLMConfig)
async def get_llm_config():
    """获取当前 LLM 配置"""
    config = get_llm_config()
    return config

@router.put("/")
async def update_llm_config(config: LLMConfig):
    """更新 LLM 配置"""
    try:
        update_llm_config(config)
        return {"message": "LLM config updated successfully"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/test-connection")
async def test_llm_connections():
    """测试所有 LLM API 连接"""
    results = await LLMFactory.test_all_connections()
    return results

@router.get("/models")
async def get_available_models():
    """获取所有可用模型信息"""
    return {
        "claude-opus-5-5": {
            "name": "Claude Opus 5.5",
            "provider": "Anthropic",
            "description": "最强代码理解能力，适合复杂分析",
            "input_cost": "$15/1M tokens",
            "output_cost": "$75/1M tokens",
            "context_window": 200000,
            "recommended_for": ["analyze", "verify"]
        },
        "grok-4.7": {
            "name": "Grok 4.7",
            "provider": "xAI (via relay)",
            "description": "性价比高，适合代码生成",
            "input_cost": "$5/1M tokens (estimated)",
            "output_cost": "$15/1M tokens (estimated)",
            "context_window": 128000,
            "recommended_for": ["propose", "fix"]
        }
    }

@router.post("/estimate-cost")
async def estimate_bug_fix_cost(config: LLMConfig):
    """预估单个 bug 修复的成本"""
    # 基于历史数据和模型定价计算
    avg_tokens = {
        "analyze": {"input": 5000, "output": 1000},
        "propose": {"input": 3000, "output": 2000},
        "fix": {"input": 4000, "output": 3000},
        "verify": {"input": 2000, "output": 500}
    }
    
    total_cost = 0.0
    breakdown = {}
    
    for stage, tokens in avg_tokens.items():
        if config.use_stage_models and config.stage_models:
            model_name = getattr(config.stage_models, stage)
        else:
            model_name = config.default_model
        
        llm = LLMFactory.create(model_name=model_name)
        model_info = llm.get_model_info()
        
        input_cost = (tokens["input"] / 1_000_000) * model_info["input_cost_per_1m"]
        output_cost = (tokens["output"] / 1_000_000) * model_info["output_cost_per_1m"]
        stage_cost = input_cost + output_cost
        
        breakdown[stage] = {
            "model": model_name,
            "cost": round(stage_cost, 4)
        }
        total_cost += stage_cost
    
    return {
        "total_cost": round(total_cost, 4),
        "currency": "USD",
        "breakdown": breakdown
    }
```

## 五、部署方案

### 4.1 开发环境

```bash
# 前端
cd frontend
npm install
npm run dev  # http://localhost:3000

# 后端
cd backend
pip install -r requirements.txt
python -m src.main  # http://localhost:8000
```

### 4.2 生产环境（Docker）

```dockerfile
# frontend/Dockerfile
FROM node:18-alpine AS builder
WORKDIR /app
COPY package*.json ./
RUN npm install
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]

# backend/Dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

```yaml
# docker-compose.yml
version: '3.8'

services:
  frontend:
    build: ./frontend
    ports:
      - "3000:80"
    depends_on:
      - backend
  
  backend:
    build: ./backend
    ports:
      - "8000:8000"
    volumes:
      - ./data:/app/data
      - ./logs:/app/logs
      - ./project:/app/project  # 挂载项目代码
    environment:
      - YUNXIAO_TOKEN=${YUNXIAO_TOKEN}
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
```

## 五、开发计划

### Week 1: 基础架构
- [ ] 前端项目搭建（React + Vite + Ant Design）
- [ ] 后端项目搭建（FastAPI + LangGraph）
- [ ] 数据库设计和初始化
- [ ] WebSocket 通信基础

### Week 2: 单 Agent 功能
- [ ] LangGraph 状态机实现
- [ ] 云效 MCP 集成
- [ ] 基础 UI 页面（Dashboard + Bug 列表）
- [ ] 单 Agent 完整流程测试

### Week 3: 多 Agent 系统
- [ ] Agent 调度器实现
- [ ] Git Worktree 隔离机制
- [ ] 任务队列和负载均衡
- [ ] Agent 监控页面

### Week 4: 优化和测试
- [ ] UI/UX 优化
- [ ] 性能优化
- [ ] 测试用例编写
- [ ] 文档完善
- [ ] V1.0 发布
