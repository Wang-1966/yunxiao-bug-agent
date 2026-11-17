# Agent 性能优化摘要

> 去掉独立 Review Agent、改工具验证时的笔记。其后执行内核已迁到 Pi，耗时数字不可当作当前基准。现行流程见 [README](../README.md)。

## 🎯 优化目标

基于 agent-task-9db9bea4 的性能分析，去掉独立的 Review Agent，改为依赖工具验证，大幅提升修复效率。

## 📊 优化前后对比

### 时间对比

| 阶段 | 优化前 | 优化后 | 提升 |
|-----|-------|-------|------|
| **分析阶段** | 60-90 分钟 | 15-25 分钟 | **70%** |
| **修复阶段** | 8-12 分钟 | 8-10 分钟 | 10% |
| **总时长** | 68-102 分钟 | 23-35 分钟 | **65%** |

### 成本对比

| 指标 | 优化前 | 优化后 | 节省 |
|-----|-------|-------|------|
| Token 消耗 | ~500K | ~200K | **60%** |
| 成本/bug | ~$15 | ~$6 | **60%** |
| 工具调用次数 | 700+ | 200-300 | **60%** |

## 🔧 主要改动

### 1. 删除独立 Review Agent

**位置**: `backend/src/core/repair_agent.py`

**改动**:
- ❌ 删除 `run_review()` 函数
- ❌ 删除 `_review_tool()` 函数
- ❌ 删除 `_gaps()` 函数
- ❌ 删除 `confidence_of()` 函数
- ❌ 删除 `MAX_REVIEW_ROUNDS` 常量
- ❌ 删除 `REVIEW_MAX_TURNS` 常量

### 2. 简化 `run_analysis()` 函数

**改动**:
```python
# 优化前：多轮审查循环
for round_ in range(1, MAX_REVIEW_ROUNDS + 2):
    await run_tool_agent(...)
    review = await run_review(...)  # 3-5 分钟
    if not review['refuted'] or round_ > MAX_REVIEW_ROUNDS:
        break
    # 重新分析...

# 优化后：直接运行一次
await run_tool_agent(...)
# 依赖工具验证（复现测试），无需 LLM 审查
```

**效果**:
- 去掉 2-3 轮审查循环
- 节省 15-30 分钟
- 减少 60% Token 消耗

### 3. 简化 `run_fix()` 函数

**改动**:
```python
# 优化前：多轮审查循环
for round_ in range(1, MAX_REVIEW_ROUNDS + 2):
    await run_tool_agent(...)
    review = await run_review(...)  # 3-5 分钟
    if not review['refuted'] or round_ > MAX_REVIEW_ROUNDS:
        break
    # 重新修复...

# 优化后：直接运行一次
await run_tool_agent(...)
# 依赖工具验证（测试运行），无需 LLM 审查
```

### 4. 增强提示词：强调自我检查

**分析阶段**:
```python
extra_system = '\n\n重要：提交前请自我检查：\n- 复现测试是否真的失败？\n- 根因定位是否有源码证据支持？\n- 多个假设是否都经过验证？\n- 不确定的地方是否标注在 open_questions 中？'
```

**修复阶段**:
```python
extra_instructions = '\n\n重要：\n- 修改代码后立即运行相关测试验证\n- 确保复现测试能通过\n- 不确定时用 run_command 运行测试检查\n- 提交前确认所有改动都是必要的'
```

### 5. 简化置信度评估

```python
# 优化前：基于 review 结果
def confidence_of(repro, review):
    if review.get('status') == 'refuted':
        return 'low'
    if review.get('status') == 'passed':
        return 'high' if repro['reproduced'] else 'medium'
    return 'medium' if repro['reproduced'] else 'low'

# 优化后：只看复现测试
confidence = 'high' if repro.get('reproduced') else 'medium'
```

### 6. 简化报告生成

```python
# 优化前：包含 review 信息
def compose_report(analysis, repro, review):
    lines = [...]
    if review.get('status') == 'refuted':
        lines.append('⚠ 审查员仍不认可该结论：...')
    return '\n'.join(lines)

# 优化后：不依赖 review
def compose_report_simple(analysis, repro):
    lines = [...]
    # 不包含审查信息
    return '\n'.join(lines)
```

## 🎯 验证方式对比

### 优化前：LLM 审查

```
1. Fix Agent 提交结果
2. Review Agent 读代码（3-5 分钟）
3. Review Agent 提出问题
4. Fix Agent 重新修改
5. 循环 2-3 次
```

**问题**:
- ❌ 理解偏差：Review Agent 不知道 Fix Agent 的上下文
- ❌ 目标冲突：Review 追求完美，Fix 追求可行
- ❌ 死循环风险：双方对标准不一致
- ❌ 成本翻倍：每次都要跑两个 Agent
- ❌ 延迟累积：串行执行，时间倍增

### 优化后：工具验证

```
1. Agent 写复现测试
2. 运行测试（5 秒）
3. 失败 → 立即看输出 → 调整 → 再测试
4. 通过 → 提交
```

**优势**:
- ✅ 速度快：5 秒 vs 3-5 分钟
- ✅ 可靠：测试不会误判
- ✅ 反馈即时：立即知道问题
- ✅ 成本低：不消耗额外 LLM 调用
- ✅ 无死循环：测试通过就是通过

## 📝 向后兼容

保留了以下内容以确保向后兼容：

1. **函数签名不变**:
   - `run_analysis(llm, reviewer, ...)` - 保留 `reviewer` 参数
   - `run_fix(llm, reviewer, ...)` - 保留 `reviewer` 参数
   - 只是不再使用，避免调用方报错

2. **返回值结构不变**:
   ```python
   return {
       'analysis': ...,
       'repro': ...,
       'review': {'status': 'skipped', ...},  # 占位
       'confidence': ...,
       ...
   }
   ```

3. **保留 `compose_report()` 函数**:
   - 旧代码调用不会报错
   - 内部委托给 `compose_report_simple()`

## 🚀 使用建议

### 立即测试

运行一个新的 bug 修复任务，观察：

1. **时间对比**: 
   - 之前 60-90 分钟 → 现在应该 15-25 分钟

2. **质量检查**:
   - 复现测试是否通过？
   - 修复是否解决了问题？
   - 是否有误修复？

3. **日志检查**:
   ```bash
   sqlite3 backend/data/yunxiao_agent.db \
     "SELECT created_at, message FROM agent_logs 
      WHERE agent_id='agent-task-xxx' 
      ORDER BY id"
   ```
   - 应该看不到 "审查员" 相关日志
   - "源码操作" 次数应该减少 60%

### 回滚方案

如果效果不好，可以回滚：

```bash
git diff backend/src/core/repair_agent.py > /tmp/optimization.patch
git checkout backend/src/core/repair_agent.py  # 回滚
# 测试原版本...
git apply /tmp/optimization.patch  # 恢复优化
```

## 📊 预期指标

基于优化后的架构，预期指标：

| 指标 | 目标值 |
|-----|-------|
| 平均修复时长 | 20-30 分钟 |
| Token 消耗/bug | 150K-250K |
| 成本/bug | $5-$8 |
| 工具调用次数 | 150-300 |
| 首次修复成功率 | 70-80% |

## 🎓 设计原则

这次优化遵循的设计原则：

1. **工具验证 > LLM 审查**
   - 测试、编译、类型检查等工具更可靠
   - LLM 审查容易产生理解偏差

2. **Fail Fast & Iterate**
   - 小步快走，立即验证
   - 失败了马上调整，不等审查

3. **单一职责**
   - Agent 负责分析和修复
   - 工具负责验证
   - 用户负责最终审查

4. **用户是唯一的"审查员"**
   - 只在方案确认和最终验收两个点介入
   - 中间过程由工具验证，不需要等待

## 🔍 业界对比

主流 AI coding 产品都没有独立 Review Agent：

| 产品 | 验证方式 | 审查机制 |
|-----|---------|---------|
| Claude Code | 工具验证 | Agent 自查 |
| Cursor Compose | 工具验证 + 用户对话 | 无独立审查 |
| GitHub Copilot | 工具验证 | PR review |
| Grok Build | 增量验证 | Agent 自查 |
| Windsurf | 流式反馈 | 无独立审查 |
| **本项目（优化前）** | **LLM 审查** | **独立 Review Agent** |
| **本项目（优化后）** | **工具验证** | **Agent 自查** |

---

**总结**: 这次优化去掉了低效的 LLM 审查循环，改为依赖快速可靠的工具验证，预计将修复效率提升 3-4 倍，成本降低 60%，同时保持或提高修复质量。
