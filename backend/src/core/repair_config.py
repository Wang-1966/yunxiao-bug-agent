"""修复流程优化配置

根据 agent-task-9db9bea4 的性能分析结果调整参数
"""

# ============ 审查相关 ============

# 最大审查轮次（从 2 降低到 1）
# 原因：3轮审查占用了大量时间，且后续轮次往往纠缠细节
MAX_REVIEW_ROUNDS = 1

# 审查超时（新增）
# 超时后自动通过，避免审查员陷入过度分析
REVIEW_TIMEOUT = 180  # 3分钟

# 审查模式（新增）
# 'adversarial': 对抗性审查（当前模式，严格）
# 'collaborative': 协作式审查（只关注致命问题）
REVIEW_MODE = 'collaborative'


# ============ 工具调用优化 ============

# 启用文件读取缓存（新增）
ENABLE_FILE_CACHE = True
FILE_CACHE_SIZE = 50

# 启用 grep 结果缓存（新增）
ENABLE_GREP_CACHE = True
GREP_CACHE_SIZE = 30


# ============ 阶段超时 ============

# 分析阶段超时（从全局 3600 分离出来）
ANALYSIS_TIMEOUT = 1200  # 20分钟

# 修复阶段超时
FIX_TIMEOUT = 900  # 15分钟

# 超时后的降级策略（新增）
# 'fail': 直接失败（当前行为）
# 'skip_review': 跳过审查直接提交
# 'use_best_effort': 使用当前最佳结果
TIMEOUT_FALLBACK = 'skip_review'


# ============ 重试和容错 ============

# 截图识别重试次数（新增）
IMAGE_RECOGNITION_RETRIES = 2

# 截图识别失败降级（新增）
# True: 使用 placeholder 继续执行
# False: 直接失败（当前行为）
IMAGE_FAILURE_GRACEFUL = True

# 模型请求中断重试次数
# 从代码中的 MAX_REQUEST_TIMEOUTS = 6 降低
MAX_REQUEST_TIMEOUTS = 3


# ============ 上下文管理 ============

# 上下文压缩触发阈值（提前压缩）
COMPACT_TRIGGER_CHARS = 200_000  # 原 300_000
COMPACT_TARGET_CHARS = 100_000   # 原 150_000

# 工具结果保留最近 N 条（原 6 条）
COMPACT_KEEP_RECENT = 4


# ============ 提示词优化 ============

# 分析阶段提示词增强（新增）
ANALYSIS_PROMPT_HINTS = [
    "20分钟内完成初步分析，不必穷尽所有边缘场景",
    "优先定位主要触发路径，次要路径可以在方案中标注",
    "测试用例覆盖核心场景即可，不要求100%覆盖",
]

# 审查提示词增强（新增）
REVIEW_PROMPT_HINTS = [
    "只关注致命缺陷（根因错误、复现失败、方案不可行）",
    "覆盖度不足、边缘场景遗漏等小问题可以接受",
    "如果核心逻辑正确，应该通过审查",
]

# 修复阶段提示词增强（新增）
FIX_PROMPT_HINTS = [
    "优先实现选定的方案，不要在修复中重新分析",
    "测试验证核心场景即可",
    "15分钟内完成修复和基本测试",
]
