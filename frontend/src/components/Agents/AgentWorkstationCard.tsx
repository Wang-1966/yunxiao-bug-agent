import React, { useState } from 'react';
import { Button, Dropdown, Progress, Space, Tag, Tooltip } from 'antd';
import {
  PauseCircleOutlined,
  PlayCircleOutlined,
  StopOutlined,
  ReloadOutlined,
  DeleteOutlined,
  DownOutlined,
  BranchesOutlined,
  FileTextOutlined,
  CodeOutlined,
  EyeOutlined,
  UpOutlined,
} from '@ant-design/icons';
import type { AgentStatus, Bug } from '../../types';
import { getStatusLabel } from '../../utils/statusLabels';
import { AgentAvatar, getAgentMood, resolveAgentPersona } from '../Shared/AgentAvatar';
import { QuestPipeline } from '../Shared/QuestPipeline';
import BugLink from '../Bugs/BugLink';
import AgentTranscript from './AgentTranscript';
import { StageTimings } from './StageTimings';

export interface AgentWorkstationCardProps {
  agent: AgentStatus;
  compact?: boolean;
  retrying?: boolean;
  stopping?: boolean;
  deleting?: boolean;
  onOpenBug?: (bugId: string) => void;
  onReview?: (agentId: string) => void;
  onRetry?: (agent: AgentStatus, fromScratch?: boolean) => void;
  onPause?: (agentId: string) => void;
  onResume?: (agentId: string) => void;
  onStop?: (agentId: string) => void;
  onDelete?: (agent: AgentStatus) => void;
}

function statusTagColor(status: string) {
  switch (status) {
    case 'busy':
      return 'processing';
    case 'idle':
      return 'success';
    case 'paused':
    case 'awaiting_confirmation':
    case 'awaiting_plan_confirmation':
      return 'warning';
    case 'error':
      return 'error';
    default:
      return 'default';
  }
}

function priorityBadge(priority?: Bug['priority']) {
  if (priority === 'critical') return { label: 'S级 · 关键悬赏', className: 'bounty-critical' };
  if (priority === 'important') return { label: 'A级 · 重要悬赏', className: 'bounty-important' };
  return { label: 'B级 · 常规悬赏', className: 'bounty-normal' };
}

export const AgentWorkstationCard: React.FC<AgentWorkstationCardProps> = ({
  agent,
  compact = false,
  retrying = false,
  stopping = false,
  deleting = false,
  onOpenBug,
  onReview,
  onRetry,
  onPause,
  onResume,
  onStop,
  onDelete,
}) => {
  const [showTranscript, setShowTranscript] = useState(
    !compact && (agent.status === 'busy' || agent.status === 'error'),
  );
  const persona = resolveAgentPersona(agent.name);
  const mood = getAgentMood(agent.status, agent.current_stage);
  const bug = agent.current_bug;
  const isWaitingAction =
    agent.status === 'awaiting_plan_confirmation' || agent.status === 'awaiting_confirmation';

  const claimable =
    !!bug &&
    (bug.assigned_agent_id === agent.agent_id
      ? bug.status === 'failed'
      : !bug.assigned_agent_id && ['pending', 'failed'].includes(bug.status));
  const retryDisabled =
    retrying || !claimable || ['已修复', '已关闭', '暂不修复'].includes(bug?.source_status || '');

  return (
    <article
      className={`workstation-card status-${agent.status} ${isWaitingAction ? 'is-attention' : ''} ${compact ? 'is-compact' : ''}`}
    >
      {/* 顶部工位铭牌与角色插画（释放横向空间，不内嵌横向气泡） */}
      <div className="workstation-header">
        <div className="workstation-identity">
          <AgentAvatar
            name={agent.name}
            status={agent.status}
            stage={agent.current_stage}
            size={compact ? 'md' : 'lg'}
            showBubble={false}
          />
          <div className="workstation-meta">
            <div className="workstation-name-row">
              <h3 className="workstation-name">{agent.name}</h3>
              <Tag color={statusTagColor(agent.status)} className="workstation-status-tag">
                {getStatusLabel(agent.status)}
              </Tag>
              <span
                className="persona-chip"
                style={{ borderColor: persona.accentColor, color: persona.accentColor }}
              >
                {persona.accessoryLabel} · {persona.roleTitle}
              </span>
            </div>
            <p className="persona-specialty">{persona.specialty}</p>
          </div>
        </div>

        {/* 右上角快捷进度环/数值 */}
        <div className="workstation-progress-pill">
          <span className="stage-pill-label">
            当前阶段：{getStatusLabel(agent.current_stage || agent.status)}
          </span>
          <Progress
            percent={agent.progress}
            size="small"
            status={
              agent.status === 'error'
                ? 'exception'
                : agent.progress >= 100
                  ? 'success'
                  : 'active'
            }
          />
        </div>
      </div>

      {/* 独立通栏心情状态对话条（不挤压标题与简介） */}
      {!compact && (
        <div className={`workstation-speech-bar ${mood.moodClass}`}>
          <span className="speech-bar-emoji" aria-hidden="true">
            {mood.emoji}
          </span>
          <span className="speech-bar-text">{mood.bubbleText}</span>
        </div>
      )}

      {/* 闯关式五阶段修复流水线 */}
      <QuestPipeline
        status={agent.status}
        stage={agent.current_stage}
        progress={agent.progress}
        compact={compact}
      />

      {/* 当前承接的 Bug 悬赏单 */}
      <div className="workstation-bug-box">
        {bug ? (
          <>
            <div className="workstation-bug-top">
              <span className={`bounty-badge ${priorityBadge(bug.priority).className}`}>
                {priorityBadge(bug.priority).label}
              </span>
              <div className="workstation-bug-title">
                <BugLink
                  bug={bug}
                  onClick={(event) => {
                    if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
                    if (onOpenBug) {
                      event.preventDefault();
                      onOpenBug(bug.bug_id);
                    }
                  }}
                />
              </div>
            </div>

            <div className="workstation-telemetry">
              {agent.repair_branch && (
                <Tooltip title={`工作树路径：${agent.worktree_path || '-'}`}>
                  <span className="telemetry-chip">
                    <BranchesOutlined /> {agent.repair_branch}
                  </span>
                </Tooltip>
              )}
              {!!agent.modified_files?.length && (
                <Tooltip title={agent.modified_files.join('、')}>
                  <span className="telemetry-chip">
                    <FileTextOutlined /> 已改 {agent.modified_files.length} 个文件
                  </span>
                </Tooltip>
              )}
              {agent.test_results?.passed !== undefined && (
                <span
                  className={`telemetry-chip ${agent.test_results.passed ? 'is-pass' : 'is-fail'}`}
                >
                  {agent.test_results.passed ? '✅ 验证通过' : '❌ 验证未通过'}
                </span>
              )}
            </div>

            {/* 阶段耗时统计 */}
            {!compact && agent.stage_timings && (
              <StageTimings
                timings={agent.stage_timings}
                currentStage={agent.current_stage}
                compact={false}
              />
            )}

            {!compact && (agent.root_cause || agent.error) && (
              <div className={`workstation-insight ${agent.error ? 'is-error' : ''}`}>
                <strong>{agent.error ? '异常诊断：' : '根因洞察：'}</strong>
                <span>{agent.error || agent.root_cause}</span>
              </div>
            )}
          </>
        ) : (
          <div className="workstation-empty-bug">暂无分配的 Bug 任务，工位待命中</div>
        )}
      </div>

      {/* 底部操控台 */}
      {!compact && (
        <div className="workstation-footer">
          <div className="workstation-footer-left">
            <Button
              size="small"
              type="text"
              icon={showTranscript ? <UpOutlined /> : <CodeOutlined />}
              onClick={() => setShowTranscript((prev) => !prev)}
            >
              {showTranscript ? '收起脑暴与执行流' : '展开实时脑暴与执行流'}
            </Button>
          </div>

          <div className="workstation-actions">
            {agent.worktree_path && onReview && (
              <Button
                size="small"
                type={isWaitingAction ? 'primary' : 'default'}
                icon={<EyeOutlined />}
                onClick={() => onReview(agent.agent_id)}
              >
                {agent.status === 'awaiting_plan_confirmation'
                  ? '审查修复方案'
                  : agent.status === 'awaiting_confirmation'
                    ? '确认修复结果'
                    : '查看修复结果'}
              </Button>
            )}

            {agent.status === 'error' && onRetry && (
              <Space.Compact size="small">
                <Button
                  size="small"
                  icon={<ReloadOutlined />}
                  loading={retrying}
                  disabled={retryDisabled}
                  title="从失败的阶段继续，复用已有工作树和分析结果"
                  onClick={() => onRetry(agent, false)}
                >
                  重新执行
                </Button>
                <Dropdown
                  disabled={retryDisabled}
                  trigger={['click']}
                  menu={{
                    items: [{ key: 'scratch', label: '从头重新执行（新建任务）' }],
                    onClick: () => onRetry(agent, true),
                  }}
                >
                  <Button size="small" icon={<DownOutlined />} disabled={retryDisabled} />
                </Dropdown>
              </Space.Compact>
            )}

            {agent.status === 'busy' && (
              <>
                {onPause && (
                  <Button
                    size="small"
                    icon={<PauseCircleOutlined />}
                    onClick={() => onPause(agent.agent_id)}
                  >
                    暂停
                  </Button>
                )}
                {onStop && (
                  <Button
                    size="small"
                    danger
                    icon={<StopOutlined />}
                    loading={stopping}
                    onClick={() => onStop(agent.agent_id)}
                  >
                    停止
                  </Button>
                )}
              </>
            )}

            {agent.status === 'paused' && onResume && (
              <Button
                size="small"
                type="primary"
                icon={<PlayCircleOutlined />}
                onClick={() => onResume(agent.agent_id)}
              >
                恢复
              </Button>
            )}

            {onDelete && (
              <Button
                size="small"
                danger
                icon={<DeleteOutlined />}
                loading={deleting}
                disabled={stopping}
                onClick={() => onDelete(agent)}
              >
                删除
              </Button>
            )}
          </div>
        </div>
      )}

      {/* 展开的实时思考与日志流 */}
      {!compact && showTranscript && (
        <div className="workstation-transcript-drawer">
          {agent.worktree_path && (
            <div className="workstation-worktree-bar">
              <span>📂 工作树：{agent.worktree_path}</span>
              {agent.modified_files?.length ? (
                <span>📝 修改文件：{agent.modified_files.join('、')}</span>
              ) : null}
            </div>
          )}
          <AgentTranscript agentId={agent.agent_id} active={agent.status === 'busy'} />
        </div>
      )}
    </article>
  );
};
