# LLM 配置设计 v2.0

> **当前行为：** 提供商在 Web UI「模型与通用配置」写入 `backend/data/llm_config.json`，不读 `CLAUDE_RELAY_*` / `GROK_RELAY_*`。上下文窗口由提供商 `context_window`/`context_length`、项目默认表或 Pi 目录自动解析，用户无需填写。实际发起模型循环的阶段是 `analyze`（分析与讨论）和 `fix`（修复）；界面上的 propose / verify 项会保存，但当前不会单独跑这两段工具循环。详见 [README](../README.md) 与 [模型窗口默认配置](../backend/docs/model-context-defaults.md)。

## 核心改进

### 1. 统一使用中转站 API
- Claude 也通过中转站访问（不写死官方 API）
- 所有 LLM 都支持自定义 Base URL 和模型名称
- 灵活切换不同的中转站和模型

### 2. 配置结构

```typescript
interface LLMProvider {
  id: string;                    // 唯一标识：claude-relay-1, grok-relay-1
  name: string;                  // 显示名称：Claude 中转站 1
  type: "openai-compatible";     // API 类型（都用 OpenAI 兼容格式）
  baseUrl: string;               // 中转站 URL
  apiKey: string;                // API Key
  availableModels: string[];     // 可用模型列表
  defaultModel: string;          // 默认模型
  enabled: boolean;              // 是否启用
}

interface LLMConfig {
  providers: LLMProvider[];      // 所有配置的提供商
  
  // 全局默认
  defaultProvider: string;       // 默认使用哪个 provider
  defaultModel: string;          // 默认模型
  
  // 分阶段配置
  useStageConfig: boolean;
  stageConfig?: {
    analyze: { provider: string; model: string; };
    propose: { provider: string; model: string; };
    fix: { provider: string; model: string; };
    verify: { provider: string; model: string; };
  };
}
```

### 3. 配置示例

```yaml
providers:
  - id: claude-relay-1
    name: Claude 中转站 1
    type: openai-compatible
    baseUrl: https://claude-relay.com/v1
    apiKey: sk-relay-xxx
    availableModels:
      - claude-opus-5-5
      - claude-sonnet-4
      - claude-haiku-4
    defaultModel: claude-opus-5-5
    enabled: true
  
  - id: grok-relay-1
    name: Grok 中转站
    type: openai-compatible
    baseUrl: https://grok-relay.com/v1
    apiKey: sk-grok-xxx
    availableModels:
      - grok-4.7
      - grok-3
    defaultModel: grok-4.7
    enabled: true
  
  - id: openai-official
    name: OpenAI 官方
    type: openai-compatible
    baseUrl: https://api.openai.com/v1
    apiKey: sk-xxx
    availableModels:
      - gpt-4
      - gpt-4-turbo
      - gpt-3.5-turbo
    defaultModel: gpt-4
    enabled: false

# 全局默认
defaultProvider: claude-relay-1
defaultModel: claude-opus-5-5

# 分阶段配置
useStageConfig: true
stageConfig:
  analyze:
    provider: claude-relay-1
    model: claude-opus-5-5
  propose:
    provider: grok-relay-1
    model: grok-4.7
  fix:
    provider: grok-relay-1
    model: grok-4.7
  verify:
    provider: claude-relay-1
    model: claude-sonnet-4
```

## UI 设计改进

### LLM 配置页面布局

```
┌─────────────────────────────────────────────────────────────┐
│  LLM 配置                                    [+ 添加提供商]  │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  提供商管理                                                  │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ 📡 Claude 中转站 1                    [启用] [编辑] │  │
│  │ Base URL: https://claude-relay.com/v1               │  │
│  │ 可用模型: claude-opus-5-5, claude-sonnet-4 等       │  │
│  │ 状态: ✅ 连接正常                    [测试连接]      │  │
│  └──────────────────────────────────────────────────────┘  │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ 🤖 Grok 中转站                       [启用] [编辑]  │  │
│  │ Base URL: https://grok-relay.com/v1                 │  │
│  │ 可用模型: grok-4.7, grok-3                          │  │
│  │ 状态: ✅ 连接正常                    [测试连接]      │  │
│  └──────────────────────────────────────────────────────┘  │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ 🔒 OpenAI 官方                       [禁用] [编辑]  │  │
│  │ Base URL: https://api.openai.com/v1                 │  │
│  │ 状态: ⚪ 未启用                                      │  │
│  └──────────────────────────────────────────────────────┘  │
│                                                              │
│  全局默认配置                                                │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ 默认提供商: [Claude 中转站 1 ▼]                     │  │
│  │ 默认模型:   [claude-opus-5-5 ▼]                     │  │
│  └──────────────────────────────────────────────────────┘  │
│                                                              │
│  分阶段配置  [⚙️ 启用]                                      │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ 分析根因阶段                                          │  │
│  │ 提供商: [Claude 中转站 1 ▼]                          │  │
│  │ 模型:   [claude-opus-5-5 ▼]                          │  │
│  │ 说明: 需要深度理解代码和业务逻辑                      │  │
│  │                                                       │  │
│  │ 设计方案阶段                                          │  │
│  │ 提供商: [Grok 中转站 ▼]                              │  │
│  │ 模型:   [grok-4.7 ▼]                                 │  │
│  │ 说明: 生成多个方案对比                                │  │
│  │                                                       │  │
│  │ 实施修复阶段                                          │  │
│  │ 提供商: [Grok 中转站 ▼]                              │  │
│  │ 模型:   [grok-4.7 ▼]                                 │  │
│  │ 说明: 编写和修改代码                                  │  │
│  │                                                       │  │
│  │ 验证修复阶段                                          │  │
│  │ 提供商: [Claude 中转站 1 ▼]                          │  │
│  │ 模型:   [claude-sonnet-4 ▼]                          │  │
│  │ 说明: 快速验证测试结果                                │  │
│  └──────────────────────────────────────────────────────┘  │
│                                                              │
│  [保存配置] [测试所有连接]                                  │
└─────────────────────────────────────────────────────────────┘
```

### 添加/编辑提供商弹窗

```
┌─────────────────────────────────────────┐
│  添加 LLM 提供商              ✕         │
├─────────────────────────────────────────┤
│                                          │
│  基本信息                                │
│  ┌────────────────────────────────────┐ │
│  │ 显示名称: [Claude 中转站 1______] │ │
│  └────────────────────────────────────┘ │
│                                          │
│  API 配置                                │
│  ┌────────────────────────────────────┐ │
│  │ Base URL:                          │ │
│  │ [https://claude-relay.com/v1_____] │ │
│  │                                    │ │
│  │ API Key:                           │ │
│  │ [sk-relay-******************____] │ │
│  └────────────────────────────────────┘ │
│                                          │
│  可用模型（一行一个）                    │
│  ┌────────────────────────────────────┐ │
│  │ claude-opus-5-5                    │ │
│  │ claude-sonnet-4                    │ │
│  │ claude-haiku-4                     │ │
│  │                                    │ │
│  └────────────────────────────────────┘ │
│                                          │
│  默认模型                                │
│  ┌────────────────────────────────────┐ │
│  │ [claude-opus-5-5 ▼]               │ │
│  └────────────────────────────────────┘ │
│                                          │
│  [测试连接] [取消] [保存]               │
└─────────────────────────────────────────┘
```

## 后端实现调整

```python
# backend/src/llm/unified_client.py
from openai import AsyncOpenAI

class UnifiedLLMClient:
    """统一的 LLM 客户端（所有 LLM 都用 OpenAI 兼容格式）"""
    
    def __init__(self, provider: LLMProvider):
        self.provider = provider
        self.client = AsyncOpenAI(
            api_key=provider.apiKey,
            base_url=provider.baseUrl
        )
    
    async def ainvoke(
        self, 
        messages: List[Message],
        model: str,
        temperature: float = 0.0,
        max_tokens: int = 8000
    ) -> str:
        response = await self.client.chat.completions.create(
            model=model,  # 使用传入的模型名称
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens
        )
        return response.choices[0].message.content
    
    async def test_connection(self) -> bool:
        try:
            await self.ainvoke(
                [{"role": "user", "content": "test"}],
                model=self.provider.defaultModel
            )
            return True
        except Exception as e:
            logger.error(f"Connection test failed: {e}")
            return False

# backend/src/llm/factory.py
class LLMFactory:
    """LLM 工厂"""
    
    @staticmethod
    def create(
        provider_id: Optional[str] = None,
        model: Optional[str] = None,
        stage: Optional[str] = None
    ) -> UnifiedLLMClient:
        """
        创建 LLM 客户端
        
        Args:
            provider_id: 提供商 ID
            model: 模型名称
            stage: 阶段（用于自动选择配置）
        """
        config = get_llm_config()
        
        # 确定使用哪个 provider 和 model
        if stage and config.useStageConfig and config.stageConfig:
            stage_cfg = getattr(config.stageConfig, stage)
            target_provider_id = stage_cfg.provider
            target_model = stage_cfg.model
        else:
            target_provider_id = provider_id or config.defaultProvider
            target_model = model or config.defaultModel
        
        # 找到对应的 provider
        provider = next(
            (p for p in config.providers if p.id == target_provider_id),
            None
        )
        
        if not provider or not provider.enabled:
            raise ValueError(f"Provider {target_provider_id} not found or disabled")
        
        client = UnifiedLLMClient(provider)
        
        # 返回一个包装，记住要使用的模型
        return LLMClientWrapper(client, target_model)

class LLMClientWrapper:
    """LLM 客户端包装器"""
    
    def __init__(self, client: UnifiedLLMClient, model: str):
        self.client = client
        self.model = model
    
    async def ainvoke(self, messages: List[Message], **kwargs) -> str:
        return await self.client.ainvoke(
            messages,
            model=self.model,  # 使用配置的模型
            **kwargs
        )
```

## 主要改进

1. ✅ **灵活的提供商管理**
   - 支持添加多个中转站
   - Claude 也通过中转站访问
   - 每个提供商可以配置多个模型

2. ✅ **动态模型切换**
   - 不写死模型名称
   - 用户可以自己输入可用模型列表
   - 下拉框动态显示

3. ✅ **统一的 API 格式**
   - 所有 LLM 都用 OpenAI 兼容格式
   - 简化实现，只需一个客户端

4. ✅ **更好的可扩展性**
   - 轻松添加新的中转站
   - 支持切换不同的中转站提供商
   - 配置灵活

这样的设计是否满足你的需求？
