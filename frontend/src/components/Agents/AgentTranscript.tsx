import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { Button, Image, Radio, message } from 'antd';
import axios from 'axios';
import { useNavigate } from 'react-router-dom';
import RichInput from '../Shared/RichInput';
import RepairReview from './RepairReview';
import BugInputMessage from './BugInputMessage';
import AnalysisDecisions from './AnalysisDecisions';
import { ArrowDownOutlined, ArrowUpOutlined, BulbOutlined, CloseCircleOutlined, CodeOutlined, ExclamationCircleOutlined, LoadingOutlined, RightOutlined, RobotOutlined, StopOutlined } from '@ant-design/icons';
import { agentService } from '../../services/agentService';
import api from '../../services/api';
import { getStatusLabel } from '../../utils/statusLabels';
import type { AgentLiveStream, AgentToolActivity, AgentTranscriptEntry, ConversationMessage, RichInputValue, Bug, AnalysisReport } from '../../types';

const seconds = (ms?: number) => `${Math.max(1, Math.round((ms || 0) / 1000))} 秒`;

const HIDDEN = /^模型请求 \d+：正在流式生成/;

const AssistantMessage: React.FC<{ children: React.ReactNode; live?: boolean }> = ({ children, live }) => (
  <div className="conversation-assistant-message">
    <div className="conversation-agent-mark" aria-hidden="true"><RobotOutlined /></div>
    <div className="conversation-assistant-body">
      <div className="conversation-speaker">修复助手{live && <span>正在回复</span>}</div>
      <div className="transcript-assistant" aria-live={live ? 'polite' : undefined}>{children}</div>
    </div>
  </div>
);

const Thinking: React.FC<{ text: string; title: string; live?: boolean }> = ({ text, title, live }) => {
  return (
    <details className={`transcript-thinking${live ? ' is-live' : ''}`}>
      <summary className="transcript-thinking-toggle"><BulbOutlined /><span>{title}</span><RightOutlined /></summary>
      <div className="transcript-thinking-body">{text}</div>
    </details>
  );
};

const Collapsible: React.FC<{ title: React.ReactNode; className: string; children: React.ReactNode }> = ({ title, className, children }) => {
  return (
    <details className={className}>
      <summary className="transcript-row-toggle">{title}<RightOutlined /></summary>
      <pre className="transcript-detail">{children}</pre>
    </details>
  );
};

const toolLabels: Record<string, string> = {
    read_file: '读取文件', grep: '搜索代码', find_files: '查找文件', list_dir: '查看目录',
    run_command: '执行命令', edit_file: '编辑文件', apply_patch: '修改代码',
    view_image: '查看图片', view_conversation_image: '查看截图', browser: '操作浏览器',
    previous_diff: '查看差异', ask_user: '澄清需求',
    submit_analysis: '提交分析', submit_fix: '提交修复', submit_reply: '提交回复', submit_merge: '提交冲突处理',
};

const ExecutionStatus: React.FC<{ activity: AgentToolActivity | null; live: AgentLiveStream | null; stage?: string; discussing: boolean; disconnected: boolean }> = ({ activity, live, stage, discussing, disconnected }) => {
  const [mountedAt] = useState(Date.now);
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  const waiting = activity?.tools.includes('ask_user');
  let label = discussing ? '正在处理你的消息' : '正在处理修复任务';
  if (stage === 'queued') label = '排队等待执行';
  if (stage === 'verifying') label = '正在验证修复';
  if (live) label = live.content ? '正在回复' : live.thinking ? '正在思考' : '等待模型响应';
  if (activity) label = activity.tools.map((name) => `正在${toolLabels[name] || '执行工具'}`).join('、');
  if (waiting) label = '等待你的回复';
  if (disconnected) label = '连接暂时中断，正在重连';
  const startedAt = activity?.started_at || live?.started_at;
  const elapsed = Math.max(0, Math.floor((now - (startedAt ? startedAt * 1000 : mountedAt)) / 1000));
  const duration = elapsed < 60 ? `${elapsed} 秒` : `${Math.floor(elapsed / 60)} 分 ${String(elapsed % 60).padStart(2, '0')} 秒`;
  return <div className={`conversation-run-status${waiting || disconnected ? ' is-waiting' : ''}`}>
    <span className="conversation-run-label" role="status">
      {waiting || disconnected ? <ExclamationCircleOutlined /> : <LoadingOutlined spin />}
      <span>{label}</span>
    </span>
    {!disconnected && <time className="conversation-run-time">已持续 {duration}</time>}
  </div>;
};

const ActivityGroup: React.FC<{ entries: AgentTranscriptEntry[] }> = ({ entries }) => {
  const tools = entries.filter((entry) => entry.message.startsWith('源码操作 '));
  const actions = [...new Set(tools.map((entry) => {
    const name = entry.message.match(/（([^）]+)）/)?.[1];
    return (name && toolLabels[name]) || '工具执行';
  }))];
  const title = tools.length ? `${actions.slice(0, 2).join('、')}${actions.length > 2 ? '等' : ''}` : '执行记录';
  return <Collapsible className="transcript-note transcript-tool-group" title={<><CodeOutlined /><span>{title}</span><span className="transcript-activity-count">{tools.length ? `${tools.length} 次操作` : `${entries.length} 条记录`}</span></>}>
    {entries.map((entry) => entry.message).join('\n\n')}
  </Collapsible>;
};

const Entry: React.FC<{ entry: AgentTranscriptEntry }> = ({ entry }) => {
  const { level, message } = entry;
  if (level === 'thinking') {
    const label = entry.metadata?.label;
    return <Thinking text={message} title={`已思考 ${seconds(entry.metadata?.duration_ms)}${label ? ` · ${label}` : ''}`} />;
  }
  if (level === 'assistant') return <AssistantMessage>{message}</AssistantMessage>;
  if (message.startsWith('已创建隔离工作树')) return <Collapsible className="transcript-note" title="已准备独立修复环境">{message}</Collapsible>;
  const stage = message.match(/^当前阶段：(\S+)$/);
  if (stage) return <div className="transcript-stage"><span>{getStatusLabel(stage[1])}</span></div>;
  const diagnostic = message.match(/^(.*?响应解析失败[^：]*)：(.*)$/s);
  if (diagnostic) {
    return <Collapsible className="transcript-note is-warning" title={<><ExclamationCircleOutlined /> {diagnostic[1]}</>}>{diagnostic[2]}</Collapsible>;
  }
  if (level === 'error') return <div className="transcript-note is-error"><CloseCircleOutlined /> {message}</div>;
  if (level === 'warning') return <div className="transcript-note is-warning"><ExclamationCircleOutlined /> {message}</div>;
  return <div className="transcript-note">{message}</div>;
};

interface ProposalSummary {
  decision_id: string;
  decision_status: string;
  root_cause?: string;
  analysis?: AnalysisReport;
  proposals: { name: string; steps?: string[]; risks?: string }[];
}

const ConversationContent: React.FC<{ agentId: string; active: boolean; stage?: string; bug?: Bug }> = ({ agentId, active, stage, bug }) => {
  const navigate = useNavigate();
  const [proposal, setProposal] = useState<ProposalSummary | null>(null);
  const [proposalError, setProposalError] = useState('');
  const [selection, setSelection] = useState<{ decisionId: string; index: number } | null>(null);
  const [submittingPlan, setSubmittingPlan] = useState(false);
  const [entries, setEntries] = useState<AgentTranscriptEntry[]>([]);
  const [live, setLive] = useState<AgentLiveStream | null>(null);
  const [activity, setActivity] = useState<AgentToolActivity | null>(null);
  const [chat, setChat] = useState<ConversationMessage[]>([]);
  const [discussing, setDiscussing] = useState(false);
  const [status, setStatus] = useState('');
  const [canSend, setCanSend] = useState(false);
  const [reviewing, setReviewing] = useState(false);
  const [draft, setDraft] = useState<RichInputValue>({ text: '', images: [] });
  const [sending, setSending] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [reading, setReading] = useState(false);
  const [error, setError] = useState('');
  const [refresh, setRefresh] = useState(0);
  const requestId = useRef<string | null>(null);
  const lastId = useRef(0);
  const scroller = useRef<HTMLDivElement>(null);
  const timelineRef = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);
  const [showLatest, setShowLatest] = useState(false);
  const scrollToLatest = () => {
    pinned.current = true;
    if (scroller.current) scroller.current.scrollTop = scroller.current.scrollHeight;
    setShowLatest(false);
  };
  const send = async () => {
    if (!canSend || submittingPlan || sending || reading || (!draft.text.trim() && !draft.images.length)) return;
    setSending(true);
    const id = requestId.current || crypto.randomUUID();
    requestId.current = id;
    try {
      const result = await agentService.sendMessage(agentId, draft, id);
      setDraft({ text: '', images: [] });
      requestId.current = null;
      setRefresh((value) => value + 1);
      scrollToLatest();
      if (result.retry) {
        if (result.retry.agent_id !== agentId) navigate(`/agents/${encodeURIComponent(result.retry.agent_id)}`);
        else setStatus('busy');
      }
    } catch (err) {
      message.error(axios.isAxiosError(err) ? err.response?.data?.detail || '发送失败，请重试' : '发送失败，请重试');
    } finally { setSending(false); }
  };
  const waitingForPlan = status === 'awaiting_plan_confirmation';
  const proposalIndex = selection?.decisionId === proposal?.decision_id ? selection?.index ?? 0 : 0;
  const canSubmitPlan = canSend && waitingForPlan && proposal?.decision_status === 'pending' && !!proposal.decision_id && !!proposal.proposals[proposalIndex] && !proposalError && !discussing;
  const hasDraft = !!draft.text.trim() || draft.images.length > 0;
  const submitPlan = async (withFeedback: boolean) => {
    if (!canSubmitPlan || !proposal || submittingPlan || sending || reading || (withFeedback && !hasDraft)) return;
    setSubmittingPlan(true);
    try {
      await agentService.confirmProposal(agentId, proposalIndex, withFeedback ? draft.text : '', proposal.decision_id,
        withFeedback ? draft.images : [], withFeedback ? draft.document : undefined);
      if (withFeedback) { setDraft({ text: '', images: [] }); requestId.current = null; }
      setStatus('busy');
      setProposal(null);
      setRefresh((value) => value + 1);
      scrollToLatest();
      message.success(withFeedback ? '反馈已提交，重新分析后会再次请你确认' : '方案已确认，开始修复');
    } catch (err) {
      message.error(axios.isAxiosError(err) ? err.response?.data?.detail || '提交失败，请重试' : '提交失败，请重试');
      setRefresh((value) => value + 1);
    } finally { setSubmittingPlan(false); }
  };
  const stop = async () => {
    if (stopping) return;
    setStopping(true);
    try { await agentService.stop(agentId); setRefresh((value) => value + 1); }
    catch { message.error('停止失败，请重试'); }
    finally { setStopping(false); }
  };


  useEffect(() => {
    let cancelled = false;
    let loading = false;
    const load = async () => {
      if (loading) return;
      loading = true;
      try {
        const [data, conversation] = await Promise.all([agentService.transcript(agentId, lastId.current), agentService.conversation(agentId)]);
        if (cancelled) return;
        if (data.entries.length) {
          lastId.current = data.entries[data.entries.length - 1].id;
          setEntries((items) => [...items, ...data.entries]);
        }
        setLive(data.live);
        setActivity(data.activity || null);
        setChat(conversation.messages);
        setDiscussing(conversation.discussing);
        setStatus(conversation.status);
        setCanSend(conversation.can_send);
        setError('');
        if (conversation.status === 'awaiting_plan_confirmation') {
          try {
            const result = await agentService.proposal(agentId);
            if (!cancelled) { setProposal(result); setProposalError(''); }
          } catch {
            if (!cancelled) setProposalError('方案暂时加载失败，正在重试。');
          }
        } else if (!cancelled) { setProposal(null); setProposalError(''); }
      } catch {
        if (!cancelled) setError('对话暂时无法加载，正在重试…');
      } finally { loading = false; }
    };
    void load();
    const timer = setInterval(load, (active || discussing) ? 1200 : 5000);
    return () => { cancelled = true; clearInterval(timer); };
  }, [agentId, active, discussing, refresh]);

  useLayoutEffect(() => {
    const el = scroller.current;
    const content = timelineRef.current;
    if (!el || !content) return;
    const observer = new ResizeObserver(() => {
      if (pinned.current) el.scrollTop = el.scrollHeight;
      const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
      if (atBottom) pinned.current = true;
      setShowLatest(!atBottom);
    });
    observer.observe(content);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const visible = entries.filter((entry) => !HIDDEN.test(entry.message) && !entry.metadata?.conversation_message_id);
  const timeline: { key: string; time: string; node: React.ReactNode; activity?: AgentTranscriptEntry }[] = [
    ...visible.map((entry) => ({ key: `log-${entry.id}`, time: entry.created_at, node: <Entry entry={entry} />,
      activity: entry.level === 'info' && !entry.message.startsWith('当前阶段：') && !entry.message.includes('响应解析失败') ? entry : undefined })),
    ...chat.map((item) => ({ key: `chat-${item.id}`, time: item.created_at, node: <div className={`conversation-message is-${item.role}`}>
      {(item.agent_id !== agentId || (item.role === 'user' && item.status === 'queued')) && <div className="conversation-meta">
        {[item.agent_id !== agentId && '之前的修复轮次', item.role === 'user' && item.status === 'queued' && '待处理'].filter(Boolean).join(' · ')}
      </div>}
      {item.role === 'assistant' ? <AssistantMessage>{item.text}</AssistantMessage> : <div className="conversation-text">{item.text}</div>}
      {!!item.images.length && <Image.PreviewGroup>{item.images.map((src, index) => <Image key={index} src={src.startsWith('/') ? `${api.defaults.baseURL}${src}` : src} alt={`补充截图 ${index + 1}`} width={160} />)}</Image.PreviewGroup>}
    </div> })),
  ].toSorted((a, b) => a.time.localeCompare(b.time));
  const groups: { key: string; node: React.ReactNode; activities?: AgentTranscriptEntry[] }[] = [];
  for (const item of timeline) {
    const previous = groups.at(-1);
    if (item.activity && previous?.activities) previous.activities.push(item.activity);
    else groups.push({ key: item.key, node: item.node, activities: item.activity ? [item.activity] : undefined });
  }

  return (
    <div className="agent-conversation">
    <div
      className="agent-transcript"
      ref={scroller}
      tabIndex={0}
      aria-label="对话记录"
      onClickCapture={(event) => {
        // 展开历史记录时保持阅读位置，不让内容增高触发自动跟随。
        if (event.target instanceof Element && event.target.closest('summary')) pinned.current = false;
      }}
      onScroll={(event) => {
        const el = event.currentTarget;
        pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
        setShowLatest(!pinned.current);
      }}
    >
      <div className="conversation-timeline" ref={timelineRef}>
      {bug && <BugInputMessage key={bug.bug_id} bug={bug} />}
      {groups.map((item) => <React.Fragment key={item.key}>{item.activities ? <ActivityGroup entries={item.activities} /> : item.node}</React.Fragment>)}
      {error && <div role="status" className="transcript-note is-warning">{error}</div>}
      {canSend && ['awaiting_plan_confirmation', 'awaiting_confirmation'].includes(status) && <div className="conversation-checkpoint">
        <strong>{status === 'awaiting_plan_confirmation' ? '修复方案待确认' : '修复已通过自动验证，等待你的验收'}</strong>
        {status === 'awaiting_plan_confirmation' ? <>
          {proposalError && <p role="status">{proposalError}</p>}
          {!proposal && !proposalError && <p>正在加载方案…</p>}
          {proposal && <div className="conversation-proposal">
            {(proposal.analysis?.root_cause || proposal.root_cause) && <p>{proposal.analysis?.root_cause || proposal.root_cause}</p>}
            <Radio.Group value={proposalIndex} disabled={submittingPlan || sending || discussing}
              onChange={(event) => setSelection({ decisionId: proposal.decision_id, index: event.target.value })}>
            {proposal.proposals.map((item, index) => <section key={index}>
              <h3><Radio value={index}>{proposal.proposals.length > 1 ? `方案 ${index + 1}：` : ''}{item.name}</Radio></h3>
              <ol>{item.steps?.map((step, i) => <li key={i}>{step}</li>)}</ol>
              {item.risks && <p>风险：{item.risks}</p>}
            </section>)}
            </Radio.Group>
            <AnalysisDecisions analysis={proposal.analysis} disabled={sending || submittingPlan || reading}
              onReply={(reply) => { setDraft((value) => ({ ...value, document: undefined, text: [value.text, reply].filter(Boolean).join('\n\n') })); requestId.current = null; }} />
          </div>}
        </> : <p>查看差异、启动预览，确认实际问题是否解决。</p>}
        {waitingForPlan ? <>
          <p className="proposal-feedback-note">在下方直接回复待确认问题或修改意见，提交后会重新分析。确认当前方案则开始修复。</p>
          <Button type="primary" loading={submittingPlan} disabled={!canSubmitPlan || sending || reading || hasDraft} onClick={() => void submitPlan(false)}>
            {proposal?.analysis?.open_questions?.length ? '按暂定方案开始修复' : '确认方案并开始修复'}
          </Button>
          {hasDraft && <p className="proposal-feedback-note">已有回复草稿，请先提交反馈，或清空后确认方案。</p>}
        </> : <Button type="primary" disabled={discussing} onClick={() => setReviewing(true)}>预览与验收</Button>}
      </div>}
      {live?.thinking && <Thinking live text={live.thinking} title={`正在思考 ${seconds(live.elapsed_ms)}`} />}
      {live?.content && <AssistantMessage live>{live.content}</AssistantMessage>}
      {!timeline.length && !live && <div className="transcript-note">修复开始后，调查进展和对话会显示在这里。你可以补充复现步骤或截图。</div>}
      </div>
    </div>
    <div className="conversation-composer">
      {showLatest && <Button className="conversation-jump-latest" icon={<ArrowDownOutlined />} onClick={scrollToLatest}>回到最新</Button>}
      {(active || status === 'busy' || discussing || live || activity) && <ExecutionStatus activity={activity} live={live} stage={stage} discussing={discussing} disconnected={!!error} />}
      <div className="conversation-input-shell">
      <RichInput variant="chat" id={`conversation-${agentId}`} value={draft} onChange={(value) => { setDraft(value); requestId.current = null; }}
        onSubmit={() => { if (waitingForPlan) void submitPlan(true); else void send(); }}
        placeholder="补充问题、添加截图，或询问进展…" maxLength={4000} disabled={sending || submittingPlan || !canSend} onBusyChange={setReading}
        footer={<div className="conversation-actions">
        {(active || discussing) && <Button type="text" icon={<StopOutlined />} loading={stopping} onClick={() => void stop()}>停止{discussing ? '回复' : '执行'}</Button>}
        {waitingForPlan && <Button type="text" disabled={!canSend || sending || submittingPlan || reading || !hasDraft} onClick={() => void send()}>仅讨论</Button>}
        {waitingForPlan ? <Button type="primary" loading={submittingPlan} disabled={!canSubmitPlan || sending || reading || !hasDraft} onClick={() => void submitPlan(true)}>提交反馈并重新分析</Button> : <Button type="primary" shape="circle" icon={<ArrowUpOutlined />} aria-label="发送消息" title="发送消息" loading={sending} disabled={!canSend || reading || (!draft.text.trim() && !draft.images.length)} onClick={() => void send()} />}
      </div>} />
      </div>
      <div className="conversation-composer-hint"><span>{!status ? (error ? '连接恢复后即可继续对话' : '正在连接会话…') : !canSend ? '历史记录 · 请在最新会话中继续' : waitingForPlan ? '回复将作为反馈，提交后重新分析' : (active || discussing) ? '可以继续补充，助手会在执行中接收' : '与修复助手继续对话'}</span><span><kbd>Enter</kbd> {waitingForPlan ? '提交反馈' : '发送'}<span className="conversation-newline-hint"> · <kbd>Shift + Enter</kbd> 换行</span></span></div>
    </div>
      <RepairReview agentId={reviewing ? agentId : null} onClose={() => setReviewing(false)} onConfirmed={() => setRefresh((value) => value + 1)} />
    </div>
  );
};

export default function AgentTranscript(props: { agentId: string; active: boolean; stage?: string; bug?: Bug }) {
  return <ConversationContent key={props.agentId} {...props} />;
}
