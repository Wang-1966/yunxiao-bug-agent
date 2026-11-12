import { useState } from 'react';
import { Link } from 'react-router-dom';
import { Button, Empty, Segmented, Tag } from 'antd';
import { ArrowRightOutlined } from '@ant-design/icons';
import type { AgentStatus } from '../../types';
import RepairSummary from './RepairSummary';

const kinds = [
  { value: 'proposal', label: '待选方案', action: '查看方案' },
  { value: 'question', label: '待回答', action: '回答问题' },
  { value: 'acceptance', label: '待验收', action: '查看验收证据' },
  { value: 'exception', label: '需处理异常', action: '查看并处理' },
];
const priorityRank = { critical: 0, important: 1, normal: 2 };
function timestamp(value?: string) {
  if (!value) return 0;
  return new Date(/(?:Z|[+-]\d\d:\d\d)$/.test(value) ? value : `${value.replace(' ', 'T')}Z`).getTime() || 0;
}
function waiting(value?: string) {
  const start = timestamp(value);
  if (!start) return '等待时长未记录';
  const minutes = Math.max(0, Math.floor((Date.now() - start) / 60000));
  if (minutes < 1) return '刚刚等待';
  if (minutes < 60) return `已等待 ${minutes} 分钟`;
  if (minutes < 1440) return `已等待 ${Math.floor(minutes / 60)} 小时 ${minutes % 60} 分钟`;
  return `已等待 ${Math.floor(minutes / 1440)} 天`;
}

export default function AttentionQueue({ agents, loading, onReview }: {
  agents: AgentStatus[]; loading: boolean; onReview: (agentId: string) => void;
}) {
  const [filter, setFilter] = useState('all');
  const pending = agents.filter((agent) => agent.attention).sort((left, right) =>
    (priorityRank[left.current_bug?.priority || 'normal'] - priorityRank[right.current_bug?.priority || 'normal']) ||
    timestamp(left.attention?.since) - timestamp(right.attention?.since) || left.agent_id.localeCompare(right.agent_id));
  const kindOf = (agent: AgentStatus) => ['error', 'delivery'].includes(agent.attention!.kind) ? 'exception' : agent.attention!.kind;
  const visible = pending.filter((agent) => filter === 'all' || kindOf(agent) === filter);
  return <section className="attention-queue" aria-label="待我处理">
    <div className="attention-heading"><div><span className="evidence-eyebrow">需要你的决定</span><h2>待我处理 <span className="attention-total">{pending.length}</span></h2></div><span className="attention-order">按优先级、等待时间排序</span></div>
    <Segmented block value={filter} onChange={(value) => setFilter(String(value))} options={[
      { value: 'all', label: `全部 ${pending.length}` },
      ...kinds.map((kind) => ({ value: kind.value, label: `${kind.label} ${pending.filter((agent) => kindOf(agent) === kind.value).length}` })),
    ]} />
    {visible.length ? <div className="attention-items">{visible.map((agent) => {
      const kind = kinds.find((item) => item.value === kindOf(agent))!;
      const reviewable = ['proposal', 'acceptance', 'delivery'].includes(agent.attention!.kind);
      return <article className="attention-item" key={agent.agent_id}>
        <div className="attention-item-main"><div className="attention-item-meta"><Tag color={agent.attention!.kind === 'acceptance' ? 'processing' : agent.attention!.kind === 'error' ? 'error' : 'warning'}>{kind.label}</Tag>
          <span>{agent.name}</span>{agent.current_bug?.priority === 'critical' && <Tag color="error">关键</Tag>}{agent.current_bug?.priority === 'important' && <Tag color="orange">重要</Tag>}
          <time dateTime={agent.attention?.since}>{waiting(agent.attention?.since)}</time></div>
          <h3>{agent.current_bug?.title || agent.name}</h3>
          {agent.attention!.kind === 'acceptance' ? <RepairSummary summary={agent.repair_summary} /> : <p>{agent.attention!.summary}</p>}
        </div>
        {reviewable ? <Button type="primary" onClick={() => onReview(agent.agent_id)}>{kind.action}<ArrowRightOutlined /></Button> : <Link to={`/agents/${encodeURIComponent(agent.agent_id)}${agent.attention!.kind === 'question' ? '#reply' : ''}`}><Button>{kind.action}<ArrowRightOutlined /></Button></Link>}
      </article>;
    })}</div> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={loading ? '正在刷新任务…' : pending.length ? '这一类暂时没有待办' : '当前没有等待你处理的任务'} />}
  </section>;
}
