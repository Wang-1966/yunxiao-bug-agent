# 修复 Agent Harness 优化方案与实施计划

> **当前实现：** `core/tool_agent.py` 与 native 编码工具循环已删除，执行内核仅为 Pi。本文件保留对照 Grok Build / Codex 时的问题清单与阶段目标，细节以 [Pi 执行内核](pi-conversation.md) 和 [README](../../README.md) 为准。

## 1. 背景与诊断

同一个 bug、同一个中转站和模型（grok-4.7），Grok Build 能修好，本项目修不好。根据运行日志和三家开源/公开 harness 的对比，问题不在模型，而在 harness：

| 问题 | 现状 | 证据（缩进 bug b2234678） |
|---|---|---|
| 工具协议 | 回复文本塞 JSON + `response_format=json_object` | 修复阶段连续 10 次“无效 JSON”被强制终止 |
| 无执行能力 | 没有 shell；验证只在 finish 后跑一次 | 不能复现、不能跑测试；`add_logging_node` 是空 TODO |
| 编辑脆弱 | `before` 必须逐字匹配，JSON 转义 | 两次 “before 必须精确匹配一次” |
| 额度过紧 | 30 次操作，最后 4 次只许 edit | 两轮因“额度不足”放弃 |
| 分析无验证 | 只读代码、根因自评置信度，下游照单全收 | 第一轮把“工具栏消失”误读为“选区高亮”，被人工驳回 |
| 验证与 bug 无关 | 验证命令仅为构建 | “验证通过”不代表修好；回归测试是源码文本断言 |
| 上下文割裂 | 分析 / 出方案（无工具）/ 修复三段独立会话 | 修复阶段被要求“不要重做根因分析” |

## 2. 借鉴来源

- **Grok Build**（Apache-2.0，Rust）：工具集 read_file / search_replace / grep / list_dir / bash；读文件 1000 行上限；编辑对易混字符归一化；`/goal` 的规划器（验收标准 + 验证方案）与对抗验证者（默认反驳、“自述不是证据”、防加码）。
- **Codex CLI**（Apache-2.0，Rust）：原生 tools 循环；shell 工具 + 平台沙箱（macOS Seatbelt）；GPT 用 `apply_patch`。
- **Claude Code**（文档）：只读工具并行；“给模型验证手段”；先写失败测试再修；全新上下文的子 agent 审查；计划模式。

只借鉴设计，在 Python 中重写，不移植代码。

## 3. 目标架构

```
fetch_bug → prepare_repository → analyze_root_cause ──(审查最多 2 轮)──┐
                                                                      ▼
                                   design_proposal（直接采用分析产出的方案）
                                                                      ▼
                                   awaiting_plan_confirmation（人工：确认症状理解 + 方案）
                                                                      ▼
                    implement_fix（续接分析会话）──(审查最多 2 轮)──→ verify_fix（构建 + 复现测试必须通过）
```

外层（云效、worktree、路径防护、验证、commit-tree、人工验收）保持不变；替换内核与分析流程。

### 3.1 原生工具调用循环（`core/tool_agent.py`）
- Chat Completions `tools` 参数；流式累积 `tool_calls`；工具结果以 `role=tool` 回传。
- 只读工具并发执行，写工具串行。
- 结束方式：模型调用 `submit_*` 工具提交结构化结果，由 harness 校验后结束；不调用工具时提醒，连续 3 次终止。
- 预算：默认 80 轮模型请求、单请求 300 秒、阶段总时限沿用 `agent_timeout`；删除“剩余额度”提示和 edit 预留。
- 上下文：只追加；超过阈值时把较早的大工具输出替换为占位（保留最近 6 条）。
- 不再写死 `temperature=0.0`，未配置时不传温度。

### 3.2 工具集（`core/agent_tools.py`）
| 工具 | 说明 |
|---|---|
| `read_file` | 单次最多 1000 行；`AGENTS.md` 整篇；允许只读 `node_modules` |
| `grep` | ripgrep，默认 200 行，可到 1000；可搜 `node_modules`（需显式 path） |
| `list_dir` | 列目录 / glob |
| `edit_file` | `old_string/new_string/replace_all`；空 `old_string` 新建；中文引号/破折号/全角空格/NBSP 等归一化匹配；失败时返回最相近片段 |
| `run_command` | macOS `sandbox-exec`：只可写 worktree 与临时/包缓存目录；禁读 `~/.ssh`、后端 `data/` 等密钥目录；精简环境变量；超时与输出截断 |
| `view_image` | 按编号重看 bug 截图 |
| `previous_diff` | 上一轮被驳回修复的差异 |
| `submit_analysis` / `submit_fix` / `submit_review` | 结构化提交 |

### 3.3 分析阶段（重点）
1. **症状契约**：必须复述期望行为、实际行为、复现步骤、验收标准；有歧义写入 `open_questions`。
2. **假设驱动**：至少 2 个候选原因，各带 `文件:行号` 证据，标注 confirmed/rejected；交互类 bug 枚举所有触发路径。
3. **复现**：分析阶段允许新增/修改测试文件和运行命令。模型提交 `reproduction.command` 后，**harness 亲自运行，必须失败**才算“已复现”；无法复现需说明原因。
4. **源码只读**：分析结束时若改动了非测试文件，拒绝提交，要求撤销。
5. **对抗审查**：全新上下文、只读工具的审查 agent 尝试推翻根因（默认反驳、防加码），最多 2 轮；仍被反驳则把审查意见展示给用户。
6. **置信度由证据决定**：已复现且审查通过 = high；未复现但审查通过 = medium；审查未通过 = low（不再直接终止，交给人工判断）。
7. **方案随分析一起产出**，`design_proposal` 不再单独调用无工具的模型。

### 3.4 修复阶段
- 续接分析会话（会话文件保存在 worktree 外），保留读过的代码与推理；会话缺失时退化为新会话 + 分析报告。
- 禁止修改复现测试文件（防止“改测试让它通过”）。
- 提交前 harness 运行复现命令必须通过；之后对抗审查 diff，最多 2 轮。

### 3.5 验证阶段
- 先跑项目验证命令，再跑复现命令；两者都通过才生成提交。复现测试随修复一起提交，成为回归测试。

## 4. 实施计划

| 阶段 | 内容 | 状态 |
|---|---|---|
| P1 | LLM 客户端支持 tools 流式；工具循环；read/grep/list/edit 工具（含归一化） | 已实现 |
| P2 | `run_command` + macOS 沙箱 + 精简环境 | 已实现 |
| P3 | 分析阶段重构：症状契约、假设、复现（harness 校验失败）、源码只读、对抗审查、证据置信度、方案合并；修复续接会话、复现必须通过、diff 审查；验证追加复现命令 | 已实现 |
| P4 | 按模型系列切换 `apply_patch`（GPT）；LLM 摘要式压缩 + 压缩后重读最近文件；Playwright 浏览器工具对接预览服务；前端展示结构化分析报告 | 已实现 |

## 5. 风险与回退
- **中转站不支持 tools**：首轮请求即报错并提示；需在 LLM 配置页做一次连接测试。
- **沙箱不可用（非 macOS）**：`run_command` 自动禁用并在提示词中说明，其余流程照常。
- **成本上升**：轮数与审查增加；审查轮数、最大轮数可通过配置调整。
- **兼容**：数据库结构、API、前端字段不变；`root_cause` 为人类可读的分析报告文本，结构化内容存于 `analysis`。
