# 模型上下文窗口默认配置

用户选择模型后自动应用窗口，无需填写。默认表为 `backend/pi/model-context-defaults.json`，覆盖当前配置的 17 个模型；其他已知模型使用 Pi 原生目录。提供商自动发现的窗口或已有管理员覆盖优先，压缩和溢出恢复仍由 Pi SDK 管理。

| 模型 | 默认窗口 tokens | 依据渠道 |
|---|---:|---|
| claude-opus-4-5 | 200000 | anthropic |
| claude-opus-4-6 / 4-7 / 4-8 / 5 / 5-5 | 1000000 | anthropic |
| claude-opus-4.8 / 5.5 | 1000000 | github-copilot 同名模型 |
| grok-4.6 / grok-4.7 | 500000 | xai |
| gpt-5.5 / gpt-5.6-sol / gpt-5.6-terra | 272000 | openai |
| gpt-6-astra / gpt-6-sol / gpt-6.1-sol / gpt-6.1-terra | 272000 | openai |

依据为已安装并锁定的 `@earendil-works/pi-coding-agent@1.5.0` 原生模型目录，2026-11-17 核对。测试将默认表逐项与该版本 Pi 目录对照，防止配置数值无依据。源项目：[earendil-works/pi](https://github.com/earendil-works/pi/tree/main/packages/ai)。

这些是对应接入渠道的默认能力值，不能声称是所有渠道均可使用的厂商最大值。例如同名 OpenAI 模型在 Pi 的 openai、azure、openai-codex 和其他渠道中可能有不同窗口；已明确匹配到其他原生渠道时采用该渠道记录，不能把最大的数字套给所有提供商。自定义中转服务可能另有限制，以其返回信息或管理员覆盖为准。

本次尝试检索 [OpenAI 模型文档](https://developers.openai.com/api/docs/models)、[Claude 模型文档](https://platform.claude.com/docs/en/about-claude/models/overview)、[xAI 模型文档](https://docs.x.ai/developers/models)；遇到 403、区域跳转或连接超时，搜索引擎结果也未提供可验证的官方规格。因此默认表明确记录 Pi 目录为数据来源，不把这些数值标为已经在线核验的官方最大值。

新增模型先核对提供商公开规格或升级 Pi 目录，更新默认表及来源。未知模型不能仅根据名称、版本号或“长上下文”宣传猜窗口，也不统一回退为固定 128K。
