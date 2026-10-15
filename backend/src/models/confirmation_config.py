"""确认策略配置模型"""
from pydantic import BaseModel, Field
from typing import Literal, Optional


class ConfirmationRules(BaseModel):
    """确认规则配置"""

    # 置信度规则
    require_on_low_confidence: bool = Field(
        default=True,
        description="置信度低时需要确认（无复现测试）"
    )

    # 方案数量规则
    require_on_multiple_proposals: bool = Field(
        default=True,
        description="多个方案时需要确认"
    )
    multiple_proposals_threshold: int = Field(
        default=2,
        description="超过此数量的方案需要确认",
        ge=1,
        le=10
    )

    # 风险规则
    require_on_high_risk: bool = Field(
        default=True,
        description="高风险修改需要确认"
    )
    high_risk_keywords: list[str] = Field(
        default_factory=lambda: [
            'database', 'db', '数据库', 'migration', '迁移',
            'auth', '认证', '授权', 'permission', 'security', '安全',
            'payment', '支付', 'money', '金额',
            'api', 'endpoint', '接口',
            'cache', '缓存', 'redis',
            'config', '配置', 'settings',
        ],
        description="高风险关键词列表"
    )

    # 改动范围规则
    require_on_large_changes: bool = Field(
        default=True,
        description="大范围改动需要确认"
    )
    large_changes_threshold: int = Field(
        default=5,
        description="超过此数量的文件改动需要确认",
        ge=1,
        le=50
    )

    # 优先级规则
    require_on_critical_priority: bool = Field(
        default=True,
        description="关键优先级 bug 需要确认"
    )

    # 未解决问题规则
    require_on_open_questions: bool = Field(
        default=True,
        description="有待确认问题时需要确认"
    )

    # 数据结构规则
    require_on_schema_changes: bool = Field(
        default=True,
        description="数据结构变更需要确认"
    )
    schema_keywords: list[str] = Field(
        default_factory=lambda: ['schema', 'model', 'entity', '表结构', '字段'],
        description="数据结构变更关键词"
    )


class ConfirmationConfig(BaseModel):
    """确认策略配置"""

    mode: Literal['always', 'smart', 'never'] = Field(
        default='smart',
        description="确认模式：always=总是确认，smart=智能确认，never=从不确认"
    )

    rules: ConfirmationRules = Field(
        default_factory=ConfirmationRules,
        description="智能确认规则（仅在 mode=smart 时生效）"
    )

    # 自定义豁免规则
    auto_approve_patterns: list[str] = Field(
        default_factory=list,
        description="自动批准的标题模式（正则表达式），例如：['UI.*', '样式.*', '文案.*']"
    )

    force_confirm_patterns: list[str] = Field(
        default_factory=list,
        description="强制确认的标题模式（正则表达式），例如：['.*生产.*', '.*线上.*']"
    )
