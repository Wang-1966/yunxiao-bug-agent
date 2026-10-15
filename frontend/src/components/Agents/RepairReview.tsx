import { useEffect, useState } from 'react';
import axios from 'axios';
import {
  Alert,
  Button,
  Collapse,
  Descriptions,
  Modal,
  Progress,
  Radio,
  Skeleton,
  Space,
  Tabs,
  Timeline,
  message,
} from 'antd';
import { agentService } from '../../services/agentService';
import type { AgentStatus, AnalysisReport, RichInputValue } from '../../types';
import { getStatusLabel } from '../../utils/statusLabels';
import RichInput from '../Shared/RichInput';
import { AgentAvatar, resolveAgentPersona } from '../Shared/AgentAvatar';
import { QuestPipeline } from '../Shared/QuestPipeline';
import RepairPreview from './RepairPreview';

interface Props {
  agentId: string | null;
  onClose: () => void;
  onConfirmed: () => void;
}

function errorText(error: unknown, fallback: string) {
  const detail = axios.isAxiosError(error) ? error.response?.data?.detail : undefined;
  return typeof detail === 'string' ? detail : fallback;
}

function confidenceLabel(confidence?: string) {
  if (confidence === 'high') return '已复现且审查通过';
  if (confidence === 'medium') return '部分证实';
  if (confidence === 'low') return '审查未通过';
  return '';
}

function AnalysisReportView({
  analysis,
  repro,
  review,
  confidence,
}: {
  analysis?: AnalysisReport | null;
  repro?: AgentStatus['repro'];
  review?: AgentStatus['review'];
  confidence?: string;
}) {
  const symptom = analysis?.symptom;
  if (!symptom) return null;
  const label = confidenceLabel(confidence);
  return (
    <section className="analysis-report">
      <div className="analysis-report-head">
        <span>症状与证据</span>
        {label && <span className={`confidence-pill is-${confidence}`}>{label}</span>}
      </div>
      <p>
        <strong>期望</strong> {symptom.expected || '-'}
      </p>
      <p>
        <strong>实际</strong> {symptom.actual || '-'}
      </p>
      {!!symptom.steps?.length && <p>复现步骤：{symptom.steps.join(' → ')}</p>}
      {!!symptom.acceptance?.length && <p>验收：{symptom.acceptance.join('；')}</p>}
      {!!analysis?.open_questions?.length && <p>待确认：{analysis.open_questions.join('；')}</p>}
      {analysis?.root_cause && <p>根因：{analysis.root_cause}</p>}
      {!!analysis?.trigger_paths?.length && <p>触发路径：{analysis.trigger_paths.join('；')}</p>}
      {!!analysis?.hypotheses?.length && (
        <ul>
          {analysis.hypotheses.map((item, index) => (
            <li key={index}>
              <span className={item.status === 'confirmed' ? 'is-confirmed' : 'is-rejected'}>
                {item.status === 'confirmed' ? '已证实' : '已排除'}
              </span>{' '}
              {item.description}（{item.evidence}）
            </li>
          ))}
        </ul>
      )}
      <p>
        {repro?.reproduced
          ? `复现命令失败（退出码 ${repro.exit_before ?? '-'}）：${repro.command}`
          : `未能自动复现：${repro?.not_reproducible_reason || '未说明'}`}
      </p>
      {review?.status === 'refuted' && (
        <p className="is-refuted">
          审查仍不认可：{(review.findings || []).map((item) => `${item.location} ${item.detail}`).join('；') || review.summary}
        </p>
      )}
      {review?.status === 'passed' && <p>审查通过{review.summary ? `：${review.summary}` : ''}</p>}
    </section>
  );
}

export default function RepairReview({ agentId, onClose, onConfirmed }: Props) {
  return agentId ? (
    <ReviewContent key={agentId} agentId={agentId} onClose={onClose} onConfirmed={onConfirmed} />
  ) : null;
}

function ReviewContent({ agentId, onClose, onConfirmed }: Props & { agentId: string }) {
  const [agent, setAgent] = useState<AgentStatus | null>(null);
  const [error, setError] = useState('');
  const [diff, setDiff] = useState<{ diff: string; truncated: boolean } | null>(null);
  const [diffError, setDiffError] = useState('');
  const [feedback, setFeedback] = useState<RichInputValue>({ text: '', images: [] });
  const [reading, setReading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [proposal, setProposal] = useState<any>(null);
  const [proposalIndex, setProposalIndex] = useState(0);

  useEffect(() => {
    if (!agentId) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const load = async () => {
      try {
        const result: AgentStatus = await agentService.get(agentId);
        if (!active) return;
        setAgent(result);
        setError('');
        if (result.status === 'awaiting_plan_confirmation') {
          try {
            const data = await agentService.proposal(agentId);
            if (active) setProposal(data);
          } catch (err) {
            if (active) setError(errorText(err, '加载修复方案失败'));
          }
        }
        if (result.status === 'busy' || result.status === 'awaiting_plan_confirmation')
          timer = setTimeout(load, 3000);
        else if (result.worktree_path) {
          try {
            const resultDiff = await agentService.diff(agentId);
            if (active) setDiff(resultDiff);
          } catch (err) {
            if (active) setDiffError(errorText(err, '读取代码差异失败'));
          }
        }
      } catch (err) {
        if (active) setError(errorText(err, '加载修复结果失败'));
      }
    };
    void load();
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [agentId]);

  const canConfirm =
    agent?.status === 'awaiting_confirmation' &&
    agent.confirmation?.status === 'pending' &&
    agent.current_bug?.assigned_agent_id === agent.agent_id;
  const canConfirmProposal =
    agent?.status === 'awaiting_plan_confirmation' && proposal?.decision_status === 'pending';
  const canRetryCleanup =
    agent?.confirmation?.user_choice?.confirmed &&
    agent.confirmation.user_choice.merge?.cleanup_pending;
  const yunxiaoSync = agent?.confirmation?.user_choice?.yunxiao_sync;

  const hasFeedback = !!feedback.text.trim() || feedback.images.length > 0;

  const confirm = async (confirmed: boolean) => {
    if (!agentId || saving || reading) return;
    if (!confirmed && !hasFeedback) {
      message.warning('请填写仍未修复的现象或复现步骤，或粘贴截图');
      return;
    }
    setSaving(true);
    try {
      const result = await agentService.confirm(
        agentId,
        confirmed,
        feedback.text,
        agent?.git_commit_hash,
        feedback.images,
        feedback.document,
      );
      if (result.merge?.cleanup_pending) message.warning(result.message);
      else if (confirmed)
        message.success(
          result.yunxiao_sync?.status === 'succeeded'
            ? '修复提交已合并，云效 Bug 已更新为处理中'
            : '修复提交已合并，本地 Bug 已标记为已解决',
        );
      else if (result.restart?.started)
        message.success(`已记录反馈，Agent 已开始第 ${result.restart.round} 轮修复`);
      else message.warning(result.restart?.reason || '已记录未修复反馈，可以手动重新执行', 6);
      if (result.yunxiao_sync?.status === 'failed')
        message.warning(
          `代码已交付，云效状态同步失败：${result.yunxiao_sync.error}。可重新打开验收详情重试同步。`,
          10,
        );
      onConfirmed();
      onClose();
    } catch (err) {
      message.error(errorText(err, '提交验收结果失败'));
    } finally {
      setSaving(false);
    }
  };

  const confirmProposal = async () => {
    if (!agentId || saving || reading || !canConfirmProposal) return;
    setSaving(true);
    try {
      await agentService.confirmProposal(
        agentId,
        proposalIndex,
        feedback.text,
        proposal.decision_id,
        feedback.images,
        feedback.document,
      );
      message.success(
        hasFeedback
          ? '反馈已提交，Agent 将重新分析并生成方案，等待你再次确认'
          : '方案已确认，Agent 开始实施修复',
      );
      onConfirmed();
      onClose();
    } catch (err) {
      message.error(errorText(err, '确认修复方案失败'));
    } finally {
      setSaving(false);
    }
  };

  const retryStatusSync = async () => {
    if (!agentId || saving || reading) return;
    setSaving(true);
    try {
      const result = await agentService.retryYunxiaoStatusSync(agentId);
      if (result.status === 'succeeded') message.success('云效 Bug 已更新为处理中');
      else message.warning(`云效状态同步失败：${result.error}`, 8);
      setAgent(await agentService.get(agentId));
      onConfirmed();
    } catch (err) {
      message.error(errorText(err, '云效状态同步失败'));
    } finally {
      setSaving(false);
    }
  };

  const persona = agent ? resolveAgentPersona(agent.name) : null;

  return (
    <Modal
      title="修复结果与人工验收"
      open={!!agentId}
      onCancel={saving || reading ? undefined : onClose}
      width={980}
      destroyOnClose
      styles={{ body: { maxHeight: 'calc(100vh - 240px)', overflowY: 'auto' } }}
      footer={
        <Space wrap>
          <Button onClick={onClose} disabled={saving || reading}>
            关闭
          </Button>
          {canConfirm && (
            <>
              <Button danger disabled={saving || reading} onClick={() => void confirm(false)}>
                仍未修复
              </Button>
              <Button
                type="primary"
                loading={saving}
                disabled={reading}
                onClick={() => void confirm(true)}
              >
                确认并合并到修复分支
              </Button>
            </>
          )}
          {canRetryCleanup && (
            <Button loading={saving} disabled={reading} onClick={() => void confirm(true)}>
              重试清理工作树
            </Button>
          )}
          {yunxiaoSync && yunxiaoSync.status !== 'succeeded' && (
            <Button loading={saving} disabled={reading} onClick={() => void retryStatusSync()}>
              重试同步云效状态
            </Button>
          )}
          {canConfirmProposal && (
            <Button
              type="primary"
              loading={saving}
              disabled={reading}
              onClick={() => void confirmProposal()}
            >
              {hasFeedback ? '提交反馈并重新分析' : '确认方案并开始修复'}
            </Button>
          )}
        </Space>
      }
    >
      {error ? (
        <Alert type="error" showIcon message={error} />
      ) : !agent ? (
        <Skeleton active />
      ) : (
        <>
          {/* 顶部特工工位横幅 + 五关进度条 */}
          <div className="workstation-header" style={{ marginBottom: 12 }}>
            <div className="workstation-identity">
              <AgentAvatar
                name={agent.name}
                status={agent.status}
                stage={agent.current_stage}
                size="md"
                showBubble
              />
              <div className="workstation-meta">
                <div className="workstation-name-row">
                  <h3 style={{ margin: 0 }}>{agent.current_bug?.title || agent.name}</h3>
                  {persona && (
                    <span
                      className="persona-chip"
                      style={{ borderColor: persona.accentColor, color: persona.accentColor }}
                    >
                      {agent.name} · {persona.roleTitle}
                    </span>
                  )}
                </div>
              </div>
            </div>
          </div>

          <QuestPipeline
            status={agent.status}
            stage={agent.current_stage}
            progress={agent.progress}
          />

          {agent.status === 'busy' && (
            <>
              <Alert
                type="info"
                showIcon
                message={`Agent 正在执行：${getStatusLabel(agent.current_stage || 'queued')}`}
              />
              <Progress percent={agent.progress} />
            </>
          )}
          {canConfirm && (
            <Alert
              type="warning"
              showIcon
              message="自动验证通过，等待你验收"
              description={`确认后将合并到 ${agent.current_bug?.branch || '项目修复分支'}，并清理 Agent 分支和工作树。${agent.current_bug?.source === 'yunxiao' ? '代码交付成功后，将云效 Bug 状态更新为处理中。' : ''}`}
            />
          )}
          {canConfirm && <RepairPreview agentId={agentId} disabled={saving} />}
          {canConfirmProposal && (
            <>
              <Alert
                type="warning"
                showIcon
                message="Agent 已完成根因分析，请先确认修复方案"
                description={
                  proposal?.analysis?.symptom
                    ? '请核对下面的症状、证据和审查结论，再选择方案。'
                    : proposal?.root_cause || agent.root_cause || '请审查分析依据后选择方案。'
                }
              />
              <AnalysisReportView
                analysis={proposal?.analysis}
                repro={proposal?.repro}
                review={proposal?.review}
                confidence={proposal?.confidence}
              />
              {proposal?.previous_attempt && (
                <Collapse
                  size="small"
                  style={{ marginTop: 12 }}
                  items={[
                    {
                      key: 'attempt',
                      label: `上一轮尝试（第 ${proposal.previous_attempt.round} 轮，已被你判定为仍未修复）`,
                      children: (
                        <>
                          <Descriptions
                            column={1}
                            size="small"
                            items={[
                              {
                                key: 'cause',
                                label: '上一轮根因',
                                children: proposal.previous_attempt.root_cause || '-',
                              },
                              {
                                key: 'plan',
                                label: '上一轮方案',
                                children: proposal.previous_attempt.selected_proposal?.name || '-',
                              },
                              {
                                key: 'files',
                                label: '修改文件',
                                children:
                                  proposal.previous_attempt.modified_files?.join('、') || '-',
                              },
                              {
                                key: 'feedback',
                                label: '你的验收反馈',
                                children: (
                                  <div style={{ whiteSpace: 'pre-wrap' }}>
                                    {proposal.previous_attempt.feedback || '（仅截图）'}
                                    {proposal.previous_attempt.image_count
                                      ? `（附 ${proposal.previous_attempt.image_count} 张截图）`
                                      : ''}
                                  </div>
                                ),
                              },
                            ]}
                          />
                          <pre className="repair-output">
                            {proposal.previous_attempt.diff || '未保存代码差异'}
                          </pre>
                        </>
                      ),
                    },
                  ]}
                />
              )}
              {proposal?.feedback && (
                <Alert
                  type="info"
                  showIcon
                  message="本轮分析已纳入的用户反馈"
                  description={<div style={{ whiteSpace: 'pre-wrap' }}>{proposal.feedback}</div>}
                  style={{ marginTop: 12 }}
                />
              )}
              <Radio.Group
                value={proposalIndex}
                onChange={(event) => setProposalIndex(event.target.value)}
                className="proposal-cards"
              >
                {(proposal?.proposals || []).map((item: any, index: number) => (
                  <div
                    key={index}
                    className={`proposal-option-card ${proposalIndex === index ? 'is-selected' : ''}`}
                    onClick={() => setProposalIndex(index)}
                  >
                    <Radio value={index}>
                      <strong>{item.name || `方案 ${index + 1}`}</strong>
                      <div className="proposal-option-meta">{item.steps?.join('；')}</div>
                      <div className="proposal-option-meta">
                        优点：{item.pros?.join('；') || '-'}；风险：{item.risks || '-'}
                      </div>
                    </Radio>
                  </div>
                ))}
              </Radio.Group>
            </>
          )}
          {agent.error && <Alert type="error" showIcon message={agent.error} />}
          {agent.confirmation?.user_choice && (
            <Alert
              showIcon
              type={agent.confirmation.user_choice.confirmed ? 'success' : 'warning'}
              message={
                agent.confirmation.user_choice.confirmed ? '你已确认修复' : '你反馈仍未修复'
              }
              description={
                <>
                  {agent.confirmation.user_choice.feedback}
                  {!!agent.confirmation.user_choice.images?.length && (
                    <div>附带 {agent.confirmation.user_choice.images.length} 张截图</div>
                  )}
                </>
              }
            />
          )}
          {yunxiaoSync && (
            <Alert
              style={{ marginTop: 12 }}
              showIcon
              type={yunxiaoSync.status === 'succeeded' ? 'success' : 'warning'}
              message={
                yunxiaoSync.status === 'succeeded'
                  ? '云效 Bug 状态已更新为处理中'
                  : '代码已交付，云效状态尚未同步完成'
              }
              description={
                yunxiaoSync.error ||
                (yunxiaoSync.status === 'pending' ? '可点击重试同步云效状态。' : undefined)
              }
            />
          )}
          <Descriptions
            column={1}
            style={{ marginTop: 16 }}
            items={[
              { key: 'summary', label: '修复说明', children: agent.fix_summary || '等待 Agent 生成' },
              {
                key: 'cause',
                label: '根因',
                children: agent.analysis?.symptom ? (
                  <AnalysisReportView analysis={agent.analysis} repro={agent.repro} review={agent.review} confidence={agent.confidence} />
                ) : (
                  agent.root_cause || '-'
                ),
              },
              { key: 'path', label: '修复工作树', children: agent.worktree_path || '-' },
              { key: 'branch', label: 'Agent 修复分支', children: agent.repair_branch || '-' },
              { key: 'commit', label: '已验证提交', children: agent.git_commit_hash || '尚未生成' },
              { key: 'targetBranch', label: '合并目标分支', children: agent.current_bug?.branch || '-' },
              { key: 'files', label: '修改文件', children: agent.modified_files?.join('、') || '-' },
              { key: 'command', label: '验证命令', children: agent.test_results?.command || '-' },
              {
                key: 'result',
                label: '验证结果',
                children:
                  agent.test_results?.passed === true
                    ? '通过（退出码 0）'
                    : agent.test_results?.passed === false
                      ? `失败（退出码 ${agent.test_results.exit_code}）`
                      : '尚未完成',
              },
            ]}
          />
          <Tabs
            items={[
              {
                key: 'diff',
                label: '代码差异',
                children: diffError ? (
                  <Alert type="warning" message={diffError} showIcon />
                ) : diff ? (
                  <>
                    {diff.truncated && (
                      <Alert
                        type="warning"
                        message="差异超过展示上限，请在工作树查看完整 Git diff"
                      />
                    )}
                    <pre className="repair-output">{diff.diff || '未产生代码差异'}</pre>
                  </>
                ) : (
                  <p>任务完成后展示代码差异。</p>
                ),
              },
              {
                key: 'verification',
                label: '验证输出',
                children: (
                  <pre className="repair-output">
                    {agent.test_results?.output || '暂无验证输出'}
                  </pre>
                ),
              },
              {
                key: 'logs',
                label: '任务日志',
                children: <Timeline items={agent.logs.map((log) => ({ children: log }))} />,
              },
            ]}
          />
          {(canConfirm || canConfirmProposal) && (
            <div>
              <label htmlFor="repair-feedback">
                {canConfirmProposal
                  ? '补充或纠正问题理解（填写后将重新分析，生成新方案再次等待确认）'
                  : '验收反馈（选择“仍未修复”时必填，可直接粘贴截图）'}
              </label>
              <RichInput
                id="repair-feedback"
                value={feedback}
                onChange={setFeedback}
                disabled={saving}
                onBusyChange={setReading}
                maxLength={4000}
                placeholder={
                  canConfirmProposal
                    ? '指出理解偏差、补充复现步骤或修复要求，截图直接粘贴在这里…'
                    : '描述仍存在的现象或验收结果，截图直接粘贴在这里…'
                }
              />
            </div>
          )}
        </>
      )}
    </Modal>
  );
}
