import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { BulbOutlined, CloseCircleOutlined, ExclamationCircleOutlined, RightOutlined } from '@ant-design/icons';
import { agentService } from '../../services/agentService';
import { getStatusLabel } from '../../utils/statusLabels';
import type { AgentLiveStream, AgentTranscriptEntry } from '../../types';

const seconds = (ms?: number) => `${Math.max(1, Math.round((ms || 0) / 1000))} 秒`;

// 工具调用和流式进度不展示；过程里只留阶段、模型说明和思考。
const HIDDEN = /^(?:源码操作 |模型请求 \d+：正在流式生成)/;

const Thinking: React.FC<{ text: string; title: string; live?: boolean }> = ({ text, title, live }) => {
  const [open, setOpen] = useState(!!live);
  useEffect(() => { if (live) setOpen(true); }, [live]);
  return (
    <div className={`transcript-thinking${live ? ' is-live' : ''}`}>
      <button type="button" className="transcript-thinking-toggle" onClick={() => setOpen(!open)} aria-expanded={open}>
        <BulbOutlined /> <span>{title}</span> <RightOutlined className={open ? 'is-open' : ''} />
      </button>
      {open && <div className="transcript-thinking-body">{text}</div>}
    </div>
  );
};

const Collapsible: React.FC<{ title: React.ReactNode; className: string; children: React.ReactNode }> = ({ title, className, children }) => {
  const [open, setOpen] = useState(false);
  return (
    <div className={className}>
      <button type="button" className="transcript-row-toggle" onClick={() => setOpen(!open)} aria-expanded={open}>
        {title} <RightOutlined className={open ? 'is-open' : ''} />
      </button>
      {open && <pre className="transcript-detail">{children}</pre>}
    </div>
  );
};

const Entry: React.FC<{ entry: AgentTranscriptEntry }> = ({ entry }) => {
  const { level, message } = entry;
  if (level === 'thinking') {
    const label = entry.metadata?.label;
    return <Thinking text={message} title={`已思考 ${seconds(entry.metadata?.duration_ms)}${label ? ` · ${label}` : ''}`} />;
  }
  if (level === 'assistant') return <div className="transcript-assistant">{message}</div>;
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

const AgentTranscript: React.FC<{ agentId: string; active: boolean }> = ({ agentId, active }) => {
  const [entries, setEntries] = useState<AgentTranscriptEntry[]>([]);
  const [live, setLive] = useState<AgentLiveStream | null>(null);
  const lastId = useRef(0);
  const scroller = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);

  useEffect(() => {
    lastId.current = 0;
    setEntries([]);
    setLive(null);
  }, [agentId]);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const data = await agentService.transcript(agentId, lastId.current);
        if (cancelled) return;
        if (data.entries.length) {
          lastId.current = data.entries[data.entries.length - 1].id;
          setEntries((items) => [...items, ...data.entries]);
        }
        setLive(data.live);
      } catch (error) {
        console.error('Failed to load transcript:', error);
      }
    };
    void load();
    const timer = setInterval(load, active ? 1200 : 5000);
    return () => { cancelled = true; clearInterval(timer); };
  }, [agentId, active]);

  useLayoutEffect(() => {
    const el = scroller.current;
    if (el && pinned.current) el.scrollTop = el.scrollHeight;
  }, [entries, live]);

  const visible = entries.filter((entry) => !HIDDEN.test(entry.message));

  return (
    <div
      className="agent-transcript"
      ref={scroller}
      onScroll={(event) => {
        const el = event.currentTarget;
        pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
      }}
    >
      {visible.map((entry) => <Entry key={entry.id} entry={entry} />)}
      {live && (live.thinking
        ? <Thinking live text={live.thinking} title={`正在思考 ${seconds(live.elapsed_ms)} · ${live.label}`} />
        : <div className="transcript-pending"><span className="transcript-dot" /> {live.content_chars ? `正在生成回复 · ${live.content_chars} 字符` : '等待模型响应'} · {live.label}</div>)}
      {live?.thinking && live.content_chars > 0 && (
        <div className="transcript-pending"><span className="transcript-dot" /> 思考完成，正在生成回复 · {live.content_chars} 字符</div>
      )}
      {!visible.length && !live && <div className="transcript-note">暂无记录</div>}
    </div>
  );
};

export default AgentTranscript;
