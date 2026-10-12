import { useEffect, useState } from 'react';
import axios from 'axios';
import { Alert, Button, Descriptions, Input, Modal, Progress, Radio, Skeleton, Space, Tabs, Timeline, message } from 'antd';
import { agentService } from '../../services/agentService';
import type { AgentStatus } from '../../types';
import { getStatusLabel } from '../../utils/statusLabels';

interface Props {
  agentId: string | null;
  onClose: () => void;
  onConfirmed: () => void;
}

function errorText(error: unknown, fallback: string) {
  const detail = axios.isAxiosError(error) ? error.response?.data?.detail : undefined;
  return typeof detail === 'string' ? detail : fallback;
}

export default function RepairReview({ agentId, onClose, onConfirmed }: Props) {
  return agentId ? <ReviewContent key={agentId} agentId={agentId} onClose={onClose} onConfirmed={onConfirmed} /> : null;
}

function ReviewContent({ agentId, onClose, onConfirmed }: Props & { agentId: string }) {
  const [agent, setAgent] = useState<AgentStatus | null>(null);
  const [error, setError] = useState('');
  const [diff, setDiff] = useState<{ diff: string; truncated: boolean } | null>(null);
  const [diffError, setDiffError] = useState('');
  const [feedback, setFeedback] = useState('');
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
          try { const data = await agentService.proposal(agentId); if (active) setProposal(data); }
          catch (err) { if (active) setError(errorText(err, '加载修复方案失败')); }
        }
        if (result.status === 'busy' || result.status === 'awaiting_plan_confirmation') timer = setTimeout(load, 3000);
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
    return () => { active = false; clearTimeout(timer); };
  }, [agentId]);

  const canConfirm = agent?.status === 'awaiting_confirmation'
    && agent.confirmation?.status === 'pending'
    && agent.current_bug?.assigned_agent_id === agent.agent_id;
  const canConfirmProposal = agent?.status === 'awaiting_plan_confirmation' && proposal?.decision_status === 'pending';
  const canRetryCleanup = agent?.confirmation?.user_choice?.confirmed
    && agent.confirmation.user_choice.merge?.cleanup_pending;

  const confirm = async (confirmed: boolean) => {
    if (!agentId || saving) return;
    if (!confirmed && !feedback.trim()) {
      message.warning('请填写仍未修复的现象或复现步骤');
      return;
    }
    setSaving(true);
    try {
      const result = await agentService.confirm(agentId, confirmed, feedback, agent?.git_commit_hash);
      if (result.merge?.cleanup_pending) message.warning(result.message);
      else message.success(confirmed ? '修复提交已合并，Bug 已标记为已解决' : '已记录未修复反馈，可以重新执行');
      onConfirmed();
      onClose();
    } catch (err) {
      message.error(errorText(err, '提交验收结果失败'));
    } finally {
      setSaving(false);
    }
  };

  const confirmProposal = async () => {
    if (!agentId || saving || !canConfirmProposal) return;
    setSaving(true);
    try {
      await agentService.confirmProposal(agentId, proposalIndex, feedback, proposal.decision_id);
      message.success(feedback.trim() ? '反馈已提交，Agent 将重新分析并生成方案，等待你再次确认' : '方案已确认，Agent 开始实施修复');
      onConfirmed();
      onClose();
    } catch (err) { message.error(errorText(err, '确认修复方案失败')); }
    finally { setSaving(false); }
  };

  return <Modal title="修复结果与人工验收" open={!!agentId} onCancel={saving ? undefined : onClose} width={960} destroyOnClose styles={{ body: { maxHeight: 'calc(100vh - 240px)', overflowY: 'auto' } }} footer={
    <Space wrap>
      <Button onClick={onClose} disabled={saving}>关闭</Button>
      {canConfirm && <>
        <Button danger disabled={saving} onClick={() => void confirm(false)}>仍未修复</Button>
        <Button type="primary" loading={saving} onClick={() => void confirm(true)}>确认并合并到修复分支</Button>
      </>}
      {canRetryCleanup && <Button loading={saving} onClick={() => void confirm(true)}>重试清理工作树</Button>}
      {canConfirmProposal && <Button type="primary" loading={saving} onClick={() => void confirmProposal()}>{feedback.trim() ? '提交反馈并重新分析' : '确认方案并开始修复'}</Button>}
    </Space>
  }>
    {error ? <Alert type="error" showIcon message={error} /> : !agent ? <Skeleton active /> : <>
      <h3>{agent.current_bug?.title || agent.name}</h3>
      {agent.status === 'busy' && <><Alert type="info" showIcon message={`Agent 正在执行：${getStatusLabel(agent.current_stage || 'queued')}`} /><Progress percent={agent.progress} /></>}
      {canConfirm && <Alert type="warning" showIcon message="自动验证通过，等待你验收" description={`确认后将合并到 ${agent.current_bug?.branch || '项目修复分支'}，并清理 Agent 分支和工作树。`} />}
      {canConfirmProposal && <>
        <Alert type="warning" showIcon message="Agent 已完成根因分析，请先确认修复方案" description={proposal?.root_cause || agent.root_cause || '请审查分析依据后选择方案。'} />
        {proposal?.feedback && <Alert type="info" showIcon message="本轮分析已纳入的用户反馈" description={<div style={{ whiteSpace: 'pre-wrap' }}>{proposal.feedback}</div>} style={{ marginTop: 12 }} />}
        <Radio.Group value={proposalIndex} onChange={(event) => setProposalIndex(event.target.value)} style={{ display: 'grid', gap: 12, margin: '16px 0' }}>
          {(proposal?.proposals || []).map((item: any, index: number) => <Radio key={index} value={index}><strong>{item.name || `方案 ${index + 1}`}</strong><div>{item.steps?.join('；')}</div><div>优点：{item.pros?.join('；') || '-'}；风险：{item.risks || '-'}</div></Radio>)}
        </Radio.Group>
      </>}
      {agent.error && <Alert type="error" showIcon message={agent.error} />}
      {agent.confirmation?.user_choice && <Alert showIcon type={agent.confirmation.user_choice.confirmed ? 'success' : 'warning'} message={agent.confirmation.user_choice.confirmed ? '你已确认修复' : '你反馈仍未修复'} description={agent.confirmation.user_choice.feedback} />}
      <Descriptions column={1} style={{ marginTop: 16 }} items={[
        { key: 'summary', label: '修复说明', children: agent.fix_summary || '等待 Agent 生成' },
        { key: 'cause', label: '根因', children: agent.root_cause || '-' },
        { key: 'path', label: '修复工作树', children: agent.worktree_path || '-' },
        { key: 'branch', label: 'Agent 修复分支', children: agent.repair_branch || '-' },
        { key: 'commit', label: '已验证提交', children: agent.git_commit_hash || '尚未生成' },
        { key: 'targetBranch', label: '合并目标分支', children: agent.current_bug?.branch || '-' },
        { key: 'files', label: '修改文件', children: agent.modified_files?.join('、') || '-' },
        { key: 'command', label: '验证命令', children: agent.test_results?.command || '-' },
        { key: 'result', label: '验证结果', children: agent.test_results?.passed === true ? '通过（退出码 0）' : agent.test_results?.passed === false ? `失败（退出码 ${agent.test_results.exit_code}）` : '尚未完成' },
      ]} />
      <Tabs items={[
        { key: 'diff', label: '代码差异', children: diffError ? <Alert type="warning" message={diffError} showIcon /> : diff ? <>{diff.truncated && <Alert type="warning" message="差异超过展示上限，请在工作树查看完整 Git diff" />}<pre className="repair-output">{diff.diff || '未产生代码差异'}</pre></> : <p>任务完成后展示代码差异。</p> },
        { key: 'verification', label: '验证输出', children: <pre className="repair-output">{agent.test_results?.output || '暂无验证输出'}</pre> },
        { key: 'logs', label: '任务日志', children: <Timeline items={agent.logs.map((log) => ({ children: log }))} /> },
      ]} />
      {(canConfirm || canConfirmProposal) && <div>
        <label htmlFor="repair-feedback">{canConfirmProposal ? '补充或纠正问题理解（填写后将重新分析，生成新方案再次等待确认）' : '验收反馈（选择“仍未修复”时必填）'}</label>
        <Input.TextArea id="repair-feedback" value={feedback} onChange={(event) => setFeedback(event.target.value)} rows={3} maxLength={4000} placeholder={canConfirmProposal ? '指出理解偏差、补充复现步骤或修复要求' : '仍存在的现象、复现步骤或其他验收说明'} style={{ marginTop: 8 }} />
      </div>}
    </>}
  </Modal>;
}
