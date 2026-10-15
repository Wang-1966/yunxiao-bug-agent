import React from 'react';
import {
  SearchOutlined,
  BulbOutlined,
  ToolOutlined,
  ExperimentOutlined,
  TrophyOutlined,
  CheckOutlined,
  CloseOutlined,
  LoadingOutlined,
} from '@ant-design/icons';

export interface QuestStep {
  key: string;
  title: string;
  subtitle: string;
  icon: React.ReactNode;
}

export const QUEST_STEPS: QuestStep[] = [
  {
    key: 'analyze',
    title: '线索侦察',
    subtitle: '拉取工作树与根因推理',
    icon: <SearchOutlined />,
  },
  {
    key: 'propose',
    title: '蓝图会审',
    subtitle: '生成方案与人工拍板',
    icon: <BulbOutlined />,
  },
  {
    key: 'fix',
    title: '手术改码',
    subtitle: '实施补丁与修改文件',
    icon: <ToolOutlined />,
  },
  {
    key: 'verify',
    title: '试炼验证',
    subtitle: '自动构建与测试闯关',
    icon: <ExperimentOutlined />,
  },
  {
    key: 'confirm',
    title: '验收通关',
    subtitle: '人工审查与分支交付',
    icon: <TrophyOutlined />,
  },
];

export function resolveQuestStepIndex(status?: string, stage?: string, progress = 0): {
  currentIndex: number;
  state: 'active' | 'waiting' | 'error' | 'completed' | 'idle';
} {
  const s = stage || status || 'idle';
  if (status === 'resolved' || stage === 'completed') {
    return { currentIndex: 4, state: 'completed' };
  }
  if (status === 'awaiting_confirmation' || s === 'awaiting_confirmation') {
    return { currentIndex: 4, state: 'waiting' };
  }
  if (status === 'awaiting_plan_confirmation' || s === 'awaiting_plan_confirmation') {
    return { currentIndex: 1, state: 'waiting' };
  }
  const baseState: 'active' | 'error' | 'idle' =
    status === 'error' || status === 'failed'
      ? 'error'
      : status === 'busy' || status === 'paused'
        ? 'active'
        : 'idle';

  if (s === 'verifying' || s === 'testing' || progress >= 75) {
    return { currentIndex: 3, state: baseState };
  }
  if (s === 'fixing' || s === 'implement_fix' || progress >= 45) {
    return { currentIndex: 2, state: baseState };
  }
  if (s === 'proposing' || s === 'design_proposal' || progress >= 30) {
    return { currentIndex: 1, state: baseState };
  }
  if (s === 'analyzing' || s === 'queued' || progress > 0) {
    return { currentIndex: 0, state: baseState };
  }
  return { currentIndex: 0, state: baseState };
}

interface QuestPipelineProps {
  status?: string;
  stage?: string;
  progress?: number;
  compact?: boolean;
}

export const QuestPipeline: React.FC<QuestPipelineProps> = ({
  status,
  stage,
  progress = 0,
  compact = false,
}) => {
  const { currentIndex, state } = resolveQuestStepIndex(status, stage, progress);

  return (
    <div className={`quest-pipeline ${compact ? 'is-compact' : ''} state-${state}`} aria-label="修复闯关进度">
      <div className="quest-steps">
        {QUEST_STEPS.map((step, idx) => {
          let stepStatus: 'done' | 'current' | 'upcoming' | 'error' | 'waiting' = 'upcoming';
          if (state === 'completed' || idx < currentIndex) {
            stepStatus = 'done';
          } else if (idx === currentIndex) {
            if (state === 'error') stepStatus = 'error';
            else if (state === 'waiting') stepStatus = 'waiting';
            else if (state === 'active') stepStatus = 'current';
          }

          return (
            <div key={step.key} className={`quest-step is-${stepStatus}`}>
              <div className="quest-node">
                {stepStatus === 'done' ? (
                  <CheckOutlined />
                ) : stepStatus === 'error' ? (
                  <CloseOutlined />
                ) : stepStatus === 'current' ? (
                  <LoadingOutlined spin />
                ) : (
                  step.icon
                )}
              </div>
              <div className="quest-step-meta">
                <div className="quest-step-title">{step.title}</div>
                {!compact && <div className="quest-step-subtitle">{step.subtitle}</div>}
              </div>
              {idx < QUEST_STEPS.length - 1 && (
                <div className={`quest-connector ${idx < currentIndex ? 'is-filled' : ''}`} />
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
};
