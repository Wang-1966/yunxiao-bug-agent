import { useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { Alert, Button, Spin, Tag, message } from 'antd';
import { ArrowLeftOutlined } from '@ant-design/icons';
import axios from 'axios';
import { agentService } from '../../services/agentService';
import { getStatusLabel } from '../../utils/statusLabels';
import type { AgentStatus } from '../../types';
import AgentTranscript from './AgentTranscript';

export default function RepairWorkspace() {
  const { agentId } = useParams();
  return agentId ? <Workspace key={agentId} agentId={agentId} /> : null;
}

function Workspace({ agentId }: { agentId: string }) {
  const navigate = useNavigate();
  const [agent, setAgent] = useState<AgentStatus | null>(null);
  const [error, setError] = useState('');
  const [refresh, setRefresh] = useState(0);
  const [retrying, setRetrying] = useState(false);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const load = async () => {
      try {
        const next: AgentStatus = await agentService.get(agentId);
        if (cancelled) return;
        setAgent(next);
        setError('');
      } catch (err) {
        if (!cancelled) setError(axios.isAxiosError(err) && err.response?.status === 404 ? '该修复任务不存在或已被删除。' : '任务信息加载失败，正在重试。');
      } finally {
        if (!cancelled) timer = setTimeout(load, 3000);
      }
    };
    void load();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [agentId, refresh]);

  const retry = async () => {
    setRetrying(true);
    try {
      const next = await agentService.retry(agentId);
      if (next.agent_id !== agentId) navigate(`/agents/${encodeURIComponent(next.agent_id)}`);
      else setRefresh((value) => value + 1);
    } catch (err) { message.error(axios.isAxiosError(err) ? err.response?.data?.detail || '重新执行失败' : '重新执行失败'); }
    finally { setRetrying(false); }
  };

  const newerAgent = agent?.current_bug?.assigned_agent_id;
  return <div className="repair-workspace">
    <header className="repair-workspace-header">
      <Link to="/agents" className="repair-workspace-back" aria-label="返回修复列表"><ArrowLeftOutlined /></Link>
      <div className="repair-workspace-title">
        <h1>{agent?.current_bug?.title || '修复工作台'}</h1>
        <div className="repair-workspace-meta">
          <span>{agent?.name || '正在加载任务'}</span>
          {agent && <Tag className={`repair-workspace-status${agent.status === 'busy' ? ' is-active' : ''}`}><span aria-hidden="true" />{getStatusLabel(agent.current_stage || agent.status)}</Tag>}
        </div>
      </div>
      {agent?.status === 'error' && (!newerAgent || newerAgent === agentId) && <Button loading={retrying} onClick={() => void retry()}>继续修复</Button>}
    </header>
    {error && <Alert type="error" message={error} />}
    {newerAgent && newerAgent !== agentId && <Alert type="info" message={<span>该 Bug 已有新一轮修复。<Link to={`/agents/${encodeURIComponent(newerAgent)}`}>进入最新会话</Link></span>} />}
    {!agent ? <div className="repair-loading">{!error && <Spin tip="正在加载修复任务"><div style={{ height: 80 }} /></Spin>}</div> : <div className="repair-workspace-body">
      <main className="repair-chat-pane" aria-label="修复对话"><AgentTranscript agentId={agentId} active={agent.status === 'busy'} stage={agent.current_stage} bug={agent.current_bug} /></main>
    </div>}
  </div>;
}
