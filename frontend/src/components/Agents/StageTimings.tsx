import React from 'react';
import { ClockCircleOutlined } from '@ant-design/icons';
import { Tooltip } from 'antd';

interface StageTimingsProps {
  timings?: {
    analyze?: number;
    fix?: number;
    verify?: number;
    total?: number;
  };
  currentStage?: string;
  compact?: boolean;
}

function formatDuration(seconds: number | undefined): string {
  if (!seconds) return '-';
  if (seconds < 60) return `${Math.round(seconds)}秒`;
  const minutes = Math.floor(seconds / 60);
  const secs = Math.round(seconds % 60);
  return secs > 0 ? `${minutes}分${secs}秒` : `${minutes}分`;
}

export const StageTimings: React.FC<StageTimingsProps> = ({
  timings,
  currentStage,
  compact = false
}) => {
  if (!timings) return null;

  const stages = [
    { key: 'analyze', label: '分析' },
    { key: 'fix', label: '修复' },
    { key: 'verify', label: '验证' },
  ];

  const hasAnyTiming = stages.some(s => timings[s.key as keyof typeof timings]);
  if (!hasAnyTiming && !timings.total) return null;

  if (compact) {
    // 紧凑模式：只显示总耗时
    if (timings.total) {
      return (
        <Tooltip title="总耗时">
          <span className="telemetry-chip timing-chip">
            <ClockCircleOutlined /> {formatDuration(timings.total)}
          </span>
        </Tooltip>
      );
    }
    return null;
  }

  // 完整模式：显示每个阶段的耗时
  return (
    <div className="stage-timings">
      <div className="stage-timings-header">
        <ClockCircleOutlined />
        <span className="stage-timings-title">阶段耗时</span>
        {timings.total && (
          <span className="stage-timings-total">
            总计 {formatDuration(timings.total)}
          </span>
        )}
      </div>

      <div className="stage-timings-list">
        {stages.map(stage => {
          const duration = timings[stage.key as keyof typeof timings];
          const isActive = currentStage === stage.key ||
                          (stage.key === 'analyze' && currentStage === 'analyzing') ||
                          (stage.key === 'fix' && currentStage === 'fixing') ||
                          (stage.key === 'verify' && currentStage === 'verifying');

          return (
            <div
              key={stage.key}
              className={`stage-timing-item ${isActive ? 'is-active' : ''} ${duration ? 'has-duration' : 'is-pending'}`}
            >
              <span className="stage-timing-label">{stage.label}</span>
              <span className="stage-timing-duration">
                {duration ? formatDuration(duration) : isActive ? '进行中...' : '-'}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
};
