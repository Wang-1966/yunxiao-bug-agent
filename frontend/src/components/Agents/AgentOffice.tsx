import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { Tooltip } from 'antd';
import type { AgentStatus } from '../../types';
import { getStatusLabel } from '../../utils/statusLabels';
import { AgentAvatar, getAgentMood, resolveAgentPersona } from '../Shared/AgentAvatar';
import { AgentMascot, OfficeChair } from '../Shared/AgentMascot';
import type { AgentLook } from '../../utils/agentLooks';
import {
  DESK,
  assignDesks,
  buildOfficeLayout,
  findPath,
  type OfficeLayout,
  type PathPoint,
  type Point,
  type Spot,
  type Zone,
  type ZoneBox,
} from './officeLayout';

/**
 * Agent 办公室：一张俯视平面图，所有 Agent 都是同一个小机器人形象（围巾颜色 + 配饰区分）。
 * - 有任务的 Agent 回到自己的工位：工作时坐着敲键盘，待拍板时站起来举手，失败时冒烟晕圈，暂停时打瞌睡
 * - 空闲的 Agent 去茶水间 / 休息区 / 健身角，并且会时不时起身换个地方
 * 状态变化时 Agent 会沿着过道走过去，而不是直接跳过去。
 */

type Pose = 'working' | 'waiting' | 'error' | 'sleeping' | 'idle';

function agentPose(agent: AgentStatus): Pose {
  if (['awaiting_plan_confirmation', 'awaiting_confirmation'].includes(agent.status)) return 'waiting';
  if (agent.status === 'error') return 'error';
  if (agent.status === 'paused') return 'sleeping';
  if (agent.status === 'busy') return 'working';
  return 'idle';
}

const WALK_SPEED = 90; // px / s
const WANDER_INTERVAL = 6500;

function prefersReducedMotion() {
  return typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
}

/** 估算文字宽度：中文 / emoji 按 1em，其余按 0.6em */
function textWidth(text: string, fontSize: number) {
  let w = 0;
  for (const ch of text) w += ch.charCodeAt(0) < 0x100 ? fontSize * 0.6 : fontSize;
  return w;
}

/** 名牌只放得下 4~5 个字：保留结尾编号，避免「断点旅行者」和「断点旅行者 2」撞名 */
function shortName(name: string, max = 4): string {
  const clean = name.trim();
  if (clean.length <= max + 1) return clean;
  const suffix = clean.match(/\s*\d+$/)?.[0].trim();
  return suffix ? `${clean.slice(0, max)}…${suffix}` : `${clean.slice(0, max)}…`;
}

function activateOnKey(event: React.KeyboardEvent, action: () => void) {
  if (event.key === 'Enter' || event.key === ' ') {
    event.preventDefault();
    action();
  }
}

function taskTip(agent: AgentStatus, extra?: string) {
  return (
    <div className="office-tip">
      <strong>
        {agent.name}
        {extra ? ` · ${extra}` : ''}
      </strong>
      <span>{agent.current_bug ? `最近任务：${agent.current_bug.title}` : '暂无任务'}</span>
    </div>
  );
}

/** SVG 里的胶囊名牌，(x, y) 为底边中心 */
function Pill({
  x,
  y,
  text,
  dot,
  className = '',
  fontSize = 10,
}: Point & { text: string; dot?: string; className?: string; fontSize?: number }) {
  const w = textWidth(text, fontSize) + (dot ? 20 : 14);
  const h = fontSize + 7;
  return (
    <g className={`office-pill ${className}`}>
      <rect x={x - w / 2} y={y - h} width={w} height={h} rx={h / 2} />
      {dot && <circle cx={x - w / 2 + 8} cy={y - h / 2} r={3} style={{ fill: dot }} />}
      <text x={x + (dot ? 3 : 0)} y={y - h / 2 + fontSize * 0.36} textAnchor="middle" style={{ fontSize }}>
        {text}
      </text>
    </g>
  );
}

/* ---------- 休息区美术：局部坐标 260 x 150 ---------- */

const ZONE_ART: Record<Zone, React.ReactNode> = {
  pantry: (
    <g>
      <rect className="office-wall" x="0" y="0" width="260" height="46" />
      <circle className="office-clock" cx="150" cy="22" r="9" />
      <path className="office-clock-hand" d="M150 22 V16 M150 22 H155" />
      <rect className="office-wood" x="176" y="26" width="72" height="4" rx="1" />
      {[182, 195, 208, 221, 234].map((x, i) => (
        <rect key={x} className={`office-cup cup-${i % 3}`} x={x} y="16" width="8" height="10" rx="2" />
      ))}
      <polygon className="office-counter-top" points="14,74 246,74 252,82 8,82" />
      <rect className="office-counter" x="8" y="82" width="244" height="26" />
      <path className="office-counter-line" d="M70 86 V104 M130 86 V104 M190 86 V104" />
      <rect className="office-machine" x="192" y="48" width="30" height="26" rx="3" />
      <rect className="office-machine-screen" x="197" y="53" width="10" height="5" rx="1" />
      <rect className="office-machine-spout" x="211" y="59" width="4" height="7" />
      <path className="office-steam" d="M210 46 q-2 -4 1 -8 M215 46 q-2 -4 1 -8" />
      <rect className="office-cup-white" x="30" y="62" width="9" height="12" rx="1.5" />
      <rect className="office-cup-white" x="41" y="65" width="9" height="9" rx="1.5" />
    </g>
  ),
  sofa: (
    <g>
      <rect className="office-wall" x="0" y="0" width="260" height="46" />
      <rect className="office-frame" x="150" y="8" width="56" height="30" rx="2" />
      <polygon className="office-art-a" points="154,34 170,18 184,34" />
      <polygon className="office-art-b" points="172,34 188,22 202,34" />
      <circle className="office-art-sun" cx="194" cy="15" r="4" />
      <ellipse className="office-rug" cx="136" cy="126" rx="112" ry="13" />
      <rect className="office-sofa-back" x="56" y="58" width="160" height="34" rx="12" />
      <rect className="office-sofa-arm" x="46" y="74" width="20" height="36" rx="8" />
      <rect className="office-sofa-arm" x="206" y="74" width="20" height="36" rx="8" />
      <rect className="office-sofa-seat" x="62" y="88" width="148" height="20" rx="6" />
      <path className="office-sofa-line" d="M110 90 V106 M162 90 V106" />
      <ellipse className="office-leaf" cx="240" cy="78" rx="7" ry="13" transform="rotate(-18 240 78)" />
      <ellipse className="office-leaf" cx="250" cy="80" rx="6" ry="11" transform="rotate(20 250 80)" />
      <rect className="office-pot" x="236" y="92" width="17" height="18" rx="3" />
    </g>
  ),
  gym: (
    <g>
      <rect className="office-wall" x="0" y="0" width="260" height="46" />
      <rect className="office-window" x="150" y="6" width="96" height="34" rx="2" />
      <path className="office-window-bar" d="M198 6 V40 M150 23 H246" />
      <path className="office-treadmill-post" d="M44 58 L42 84 M72 58 L74 84" />
      <rect className="office-machine" x="38" y="50" width="40" height="11" rx="3" />
      <rect className="office-machine-screen" x="50" y="53" width="16" height="5" rx="1" />
      <polygon className="office-treadmill-belt" points="40,84 76,84 90,116 26,116" />
      <rect className="office-treadmill-base" x="24" y="116" width="68" height="5" rx="2" />
      <ellipse className="office-mat" cx="138" cy="116" rx="24" ry="6" />
      <ellipse className="office-beanbag" cx="214" cy="96" rx="32" ry="22" />
      <rect className="office-dumbbell-bar" x="112" y="62" width="22" height="3" rx="1" />
      <rect className="office-dumbbell" x="108" y="58" width="6" height="11" rx="1.5" />
      <rect className="office-dumbbell" x="132" y="58" width="6" height="11" rx="1.5" />
    </g>
  ),
};

function ZoneArt({ zone }: { zone: ZoneBox }) {
  return (
    <g transform={`translate(${zone.x} ${zone.y})`}>
      <rect className="office-zone-floor" width={zone.w} height={zone.h} rx={8} />
      {ZONE_ART[zone.zone]}
      <Pill x={10 + (textWidth(zone.title, 10) + 14) / 2} y={22} text={zone.title} className="is-room" />
    </g>
  );
}

/* ---------- 工位：局部坐标 150 x 150，座位在 (75, 112) ---------- */

type Screen = Pose | 'away' | 'off';

function ScreenContent({ screen, stage }: { screen: Screen; stage?: string }) {
  switch (screen) {
    case 'working':
      return (
        <g className={`office-code stage-${stage || 'busy'}`}>
          <rect className="office-code-line l1" x="54" y="48" width="22" height="2.6" rx="1" />
          <rect className="office-code-line l2" x="58" y="54" width="28" height="2.6" rx="1" />
          <rect className="office-code-line l3" x="58" y="60" width="18" height="2.6" rx="1" />
          <rect className="office-code-line l4" x="54" y="66" width="26" height="2.6" rx="1" />
          <rect className="office-cursor" x="82" y="65" width="3" height="4" />
        </g>
      );
    case 'waiting':
      return (
        <g className="office-checklist">
          <path d="M55 50 l2 2 l4 -4 M55 58 l2 2 l4 -4 M55 66 l2 2 l4 -4" />
          <rect x="65" y="49" width="26" height="2.6" rx="1" />
          <rect x="65" y="57" width="20" height="2.6" rx="1" />
          <rect x="65" y="65" width="24" height="2.6" rx="1" />
        </g>
      );
    case 'error':
      return (
        <text className="office-alert" x="75" y="66" textAnchor="middle">
          !
        </text>
      );
    case 'idle':
      return <circle className="office-saver" cx="75" cy="56" r="4" />;
    case 'away':
      return (
        <g transform="rotate(-6 75 56)">
          <rect className="office-sticky" x="63" y="47" width="24" height="17" rx="1" />
          <path className="office-sticky-line" d="M67 53 H83 M67 58 H78" />
        </g>
      );
    default:
      return null;
  }
}

interface DeskArtProps {
  desk: Point;
  owner?: AgentStatus;
  look?: AgentLook;
  /** 主人的目标位置在本工位（座位或旁边站位） */
  atDesk: boolean;
  /** 主人已经坐在座位上：椅子由角色自己画 */
  seated: boolean;
  awayActivity?: string;
  dimmed: boolean;
  selected: boolean;
  onSelect: (agentId: string) => void;
  onReview: (agentId: string) => void;
}

function DeskArt({ desk, owner, look, atDesk, seated, awayActivity, dimmed, selected, onSelect, onReview }: DeskArtProps) {
  const pose = owner ? agentPose(owner) : undefined;
  const screen: Screen = !owner || !pose ? 'off' : atDesk ? pose : 'away';

  let bubble: { text: string; tone: string; action?: () => void } | null = null;
  if (owner && atDesk && pose === 'working') {
    const mood = getAgentMood(owner.status, owner.current_stage);
    bubble = { text: `${mood.emoji} ${getStatusLabel(owner.current_stage || owner.status)} · ${owner.progress}%`, tone: 'is-working' };
  } else if (owner && atDesk && pose === 'waiting')
    bubble = {
      text: owner.status === 'awaiting_plan_confirmation' ? '📋 方案待拍板' : '🏆 待你验收',
      tone: 'is-action',
      action: () => (owner.worktree_path ? onReview(owner.agent_id) : onSelect(owner.agent_id)),
    };
  else if (owner && atDesk && pose === 'error') bubble = { text: '💫 踩坑了', tone: 'is-error' };
  else if (owner && atDesk && pose === 'sleeping') bubble = { text: '💤 已暂停', tone: 'is-muted' };

  const bubbleW = bubble ? Math.min(DESK.w - 4, textWidth(bubble.text, 11) + 18) : 0;
  const nameText = owner ? (owner.name.length > 8 ? shortName(owner.name, 6) : owner.name) : '虚位以待';

  const art = (
    <g
      transform={`translate(${desk.x} ${desk.y})`}
      className={`office-desk ${owner ? 'is-owned' : 'is-empty'} ${dimmed ? 'is-dimmed' : ''} ${selected ? 'is-selected' : ''}`}
      role={owner ? 'button' : undefined}
      tabIndex={owner ? 0 : undefined}
      aria-label={owner ? `${owner.name}的工位：${getStatusLabel(owner.status)}，点击查看详情` : undefined}
      onClick={owner ? () => onSelect(owner.agent_id) : undefined}
      onKeyDown={owner ? (event) => activateOnKey(event, () => onSelect(owner.agent_id)) : undefined}
    >
      <rect className="office-desk-hit" x="-4" y="-8" width={DESK.w + 8} height={DESK.laneY - 4} rx="10" />
      {pose === 'waiting' && atDesk && <ellipse className="office-spotlight" cx={DESK.stand.x} cy={DESK.stand.y} rx="24" ry="8" />}
      {/* 绿植 */}
      <ellipse className="office-leaf" cx="18" cy="70" rx="5" ry="10" transform="rotate(-16 18 70)" />
      <ellipse className="office-leaf" cx="26" cy="71" rx="4.5" ry="9" transform="rotate(18 26 71)" />
      <rect className="office-pot" x="15" y="76" width="14" height="10" rx="2" />
      {/* 显示器 */}
      <rect className="office-stand" x="72" y="78" width="6" height="8" />
      <ellipse className="office-stand" cx="75" cy="86" rx="12" ry="2.5" />
      <rect className="office-monitor" x="47" y="40" width="56" height="38" rx="3" />
      <rect className={`office-screen screen-${screen}`} x="50" y="43" width="50" height="32" rx="1.5" />
      <ScreenContent screen={screen} stage={owner?.current_stage} />
      {/* 桌子 */}
      <polygon className="office-desk-top" points="12,84 138,84 146,98 4,98" />
      <rect className="office-desk-front" x="4" y="98" width="142" height="6" />
      <rect className="office-desk-leg" x="8" y="104" width="4" height="22" />
      <rect className="office-desk-leg" x="138" y="104" width="4" height="22" />
      <rect className="office-desk-cabinet" x="104" y="104" width="32" height="22" rx="2" />
      <path className="office-desk-line" d="M104 115 H136" />
      <rect className="office-mug" x="116" y="76" width="9" height="9" rx="2" />
      <path className="office-mug-handle" d="M125 78.5 h2 a2 2 0 0 1 0 4 h-2" />
      {screen === 'idle' && <path className="office-steam" d="M118 73 q-2 -4 1 -8 M122 73 q-2 -4 1 -8" />}
      {pose === 'working' && atDesk && owner && (
        <g>
          <rect className="office-progress-track" x="4" y="100" width="142" height="3" />
          <rect className="office-progress-fill" x="4" y="100" width={(142 * Math.min(100, Math.max(0, owner.progress))) / 100} height="3" />
        </g>
      )}
      {screen === 'error' && (
        <g className="office-smoke">
          <circle cx="58" cy="38" r="4" />
          <circle cx="66" cy="33" r="5" />
          <circle cx="92" cy="37" r="3.5" />
        </g>
      )}
      {/* 椅子：主人坐下后由角色自己画，否则往左推开（右边是待拍板时的站位） */}
      {!seated && (
        <g transform={owner ? `translate(${DESK.seat.x - 22} ${DESK.seat.y + 4}) rotate(-12)` : `translate(${DESK.seat.x} ${DESK.seat.y})`}>
          <OfficeChair />
        </g>
      )}
      <Pill
        x={75}
        y={36}
        text={nameText}
        dot={look?.color}
        fontSize={11}
        className={!owner ? 'is-empty' : !atDesk ? 'is-away' : ''}
      />
      {bubble && (
        <g
          className={`office-bubble ${bubble.tone}`}
          role={bubble.action ? 'button' : undefined}
          tabIndex={bubble.action ? 0 : undefined}
          onClick={
            bubble.action
              ? (event) => {
                  event.stopPropagation();
                  bubble?.action?.();
                }
              : undefined
          }
          onKeyDown={
            bubble.action
              ? (event) => {
                  event.stopPropagation();
                  activateOnKey(event, () => bubble?.action?.());
                }
              : undefined
          }
        >
          <rect x={75 - bubbleW / 2} y={-7} width={bubbleW} height={20} rx={9} />
          <path d="M71 12.5 L75 17 L79 12.5 Z" />
          <text x={75} y={6.8} textAnchor="middle">
            {bubble.text}
          </text>
        </g>
      )}
    </g>
  );

  if (!owner) return art;
  const extra = atDesk ? getStatusLabel(owner.current_stage || owner.status) : awayActivity;
  return (
    <Tooltip title={taskTip(owner, extra)} mouseEnterDelay={0.35}>
      {art}
    </Tooltip>
  );
}

/* ---------- 会走路的 Agent ---------- */

type Facing = 'down' | 'up' | 'left' | 'right';

interface ActorProps {
  agent: AgentStatus;
  look: AgentLook;
  target: string;
  layout: OfficeLayout;
  dimmed: boolean;
  selected: boolean;
  onSelect: (agentId: string) => void;
  onRest: (agentId: string, spotKey: string | null) => void;
}

function ActorBody({ spot, agent, look }: { spot: Spot; agent: AgentStatus; look: AgentLook }) {
  const pose = agentPose(agent);
  if (spot.kind === 'seat') {
    return (
      <g>
        <AgentMascot
          look={look}
          view="back"
          arms={pose === 'working' ? 'type' : 'down'}
          className={pose === 'working' ? 'is-typing' : pose === 'sleeping' ? 'office-doze' : ''}
        />
        <OfficeChair />
        {pose === 'error' && (
          <g className="office-dizzy">
            <path d="M-10 -62 l2 -5 l2 5 l5 2 l-5 2 l-2 5 l-2 -5 l-5 -2 Z" />
            <path d="M10 -66 l1.5 -4 l1.5 4 l4 1.5 l-4 1.5 l-1.5 4 l-1.5 -4 l-4 -1.5 Z" />
          </g>
        )}
        {pose === 'sleeping' && (
          <g className="office-zzz">
            <text x="16" y="-46">z</text>
            <text x="24" y="-56">Z</text>
          </g>
        )}
      </g>
    );
  }
  if (spot.kind === 'stand')
    return (
      <g className="office-hop">
        <AgentMascot look={look} face="happy" arms="up" />
      </g>
    );
  switch (spot.pose) {
    case 'cup':
      return <AgentMascot look={look} arms="cup" className="office-sway" />;
    case 'chat':
      return <AgentMascot look={look} face="happy" className="office-sway is-alt" />;
    case 'brew':
      return <AgentMascot look={look} view="back" arms="type" className="is-typing" />;
    case 'sofa':
      return <AgentMascot look={look} face={spot.key === 'sofa-1' ? 'sleepy' : 'happy'} sitting className="office-breathe" />;
    case 'run':
      return <AgentMascot look={look} view="back" walking className="is-running" />;
    case 'beanbag':
      return (
        <g transform="rotate(-10)">
          <AgentMascot look={look} face="sleepy" sitting />
        </g>
      );
    case 'stretch':
      return <AgentMascot look={look} face="happy" arms="up" className="office-stretch" />;
    default:
      return <AgentMascot look={look} />;
  }
}

const Actor: React.FC<ActorProps> = ({ agent, look, target, layout, dimmed, selected, onSelect, onRest }) => {
  const gRef = useRef<SVGGElement>(null);
  const nodeRef = useRef<string | null>(null);
  const posRef = useRef<Point>({ x: 0, y: 0 });
  const animRef = useRef<Animation | null>(null);
  const runRef = useRef(0);
  const walkingRef = useRef(false);
  const targetRef = useRef(target);
  targetRef.current = target;
  const [restingAt, setRestingAt] = useState<string | null>(null);
  const [facing, setFacing] = useState<Facing>('down');

  const place = (p: Point) => {
    posRef.current = { x: p.x, y: p.y };
    if (gRef.current) gRef.current.style.transform = `translate(${p.x}px, ${p.y}px)`;
  };

  const settle = (key: string) => {
    walkingRef.current = false;
    setRestingAt(key);
    setFacing('down');
    onRest(agent.agent_id, key);
  };

  const teleport = (key: string) => {
    runRef.current += 1;
    animRef.current?.cancel();
    walkingRef.current = false;
    const spot = layout.spots.get(key);
    if (!spot) return;
    nodeRef.current = key;
    place(spot);
    settle(key);
  };

  // 首次出现或布局变化（窗口缩放）：直接出现在目标位置
  useLayoutEffect(() => {
    teleport(targetRef.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [layout.signature]);

  /** 每走到一个路口重新算一次路线，所以半路换目标也能自然转弯 */
  const walk = async () => {
    if (walkingRef.current) return;
    walkingRef.current = true;
    const run = ++runRef.current;
    setRestingAt(null);
    onRest(agent.agent_id, null);
    while (runRef.current === run && nodeRef.current !== targetRef.current) {
      const path = findPath(layout.graph, nodeRef.current!, targetRef.current);
      const next: PathPoint | undefined = path[1];
      if (!next) break;
      const from = posRef.current;
      const dx = next.x - from.x;
      const dy = next.y - from.y;
      const distance = Math.abs(dx) + Math.abs(dy);
      if (distance > 0.5) {
        setFacing(Math.abs(dy) > Math.abs(dx) ? (dy < 0 ? 'up' : 'down') : dx < 0 ? 'left' : 'right');
        const anim = gRef.current?.animate(
          [
            { transform: `translate(${from.x}px, ${from.y}px)` },
            { transform: `translate(${next.x}px, ${next.y}px)` },
          ],
          { duration: (distance / WALK_SPEED) * 1000, easing: 'linear', fill: 'forwards' },
        );
        animRef.current = anim || null;
        try {
          await anim?.finished;
        } catch {
          return; // 被瞬移打断
        }
        if (runRef.current !== run) return;
      }
      place(next);
      clearAnimation(animRef);
      nodeRef.current = next.node;
    }
    if (runRef.current !== run) return;
    if (nodeRef.current !== targetRef.current) teleport(targetRef.current);
    else settle(targetRef.current);
  };

  useEffect(() => {
    if (!nodeRef.current || nodeRef.current === target) return;
    if (prefersReducedMotion()) teleport(target);
    else void walk();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target]);

  useEffect(
    () => () => {
      runRef.current += 1;
      animRef.current?.cancel();
      onRest(agent.agent_id, null);
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  const spot = restingAt ? layout.spots.get(restingAt) : undefined;
  const walking = !spot;
  const persona = resolveAgentPersona(agent.name);
  const showLabel = walking || spot?.kind === 'lounge';
  // 名牌放在天线灯上方
  const labelY = spot?.kind === 'lounge' ? (spot.labelDy ?? -66) : -66;
  const tipExtra = walking ? '走动中' : spot?.activity || getStatusLabel(agent.current_stage || agent.status);

  return (
    <g ref={gRef} className={`office-actor ${dimmed ? 'is-dimmed' : ''} ${selected ? 'is-selected' : ''}`}>
      <Tooltip title={taskTip(agent, tipExtra)} mouseEnterDelay={0.25}>
        <g
          className="office-actor-hit"
          role="button"
          tabIndex={0}
          aria-label={`${agent.name}，${tipExtra}，点击查看工位详情`}
          onClick={() => onSelect(agent.agent_id)}
          onKeyDown={(event) => activateOnKey(event, () => onSelect(agent.agent_id))}
        >
          <ellipse className="office-actor-shadow" cx="0" cy="0" rx="15" ry="4" />
          {walking ? (
            <g transform={facing === 'left' ? 'scale(-1 1)' : undefined}>
              <g transform={facing === 'left' || facing === 'right' ? 'rotate(4)' : undefined}>
                <AgentMascot look={look} view={facing === 'up' ? 'back' : 'front'} walking />
              </g>
            </g>
          ) : (
            <ActorBody spot={spot} agent={agent} look={look} />
          )}
          {showLabel && <Pill x={0} y={labelY} text={shortName(agent.name)} dot={persona.accentColor} fontSize={9} className="is-actor" />}
        </g>
      </Tooltip>
    </g>
  );
};

function clearAnimation(ref: React.MutableRefObject<Animation | null>) {
  // fill: forwards 的动画会一直覆盖 style.transform，走完一段就取消掉，位置由 place() 接管
  ref.current?.cancel();
  ref.current = null;
}

/* ---------- 办公室主体 ---------- */

const FEED_ORDER: Record<Pose, number> = { waiting: 0, error: 1, working: 2, sleeping: 3, idle: 4 };

export interface AgentOfficeProps {
  agents: AgentStatus[];
  /** 不符合当前筛选条件的 Agent 会被淡化，而不是从办公室里消失 */
  isHighlighted: (agent: AgentStatus) => boolean;
  selectedId?: string | null;
  onSelect: (agentId: string) => void;
  onReview: (agentId: string) => void;
}

export const AgentOffice: React.FC<AgentOfficeProps> = ({ agents, isHighlighted, selectedId, onSelect, onReview }) => {
  const wrapRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  useLayoutEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    setWidth(el.clientWidth);
    const observer = new ResizeObserver(([entry]) => setWidth(Math.round(entry.contentRect.width)));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  // 姓名序固定工位。排在工位数量之外但正在干活的人，换掉最后一名空闲同事，否则座位不存在，人会停在茶水间
  const sorted = useMemo(() => [...agents].sort((a, b) => a.name.localeCompare(b.name, 'zh-CN')), [agents]);
  const layout = useMemo(() => (width ? buildOfficeLayout(width, sorted.length) : null), [width, sorted.length]);
  const deskOf = useMemo(
    () =>
      assignDesks(
        sorted.map((agent) => ({ agent_id: agent.agent_id, idle: agentPose(agent) === 'idle' })),
        layout?.desks.length ?? 0,
      ),
    [sorted, layout],
  );

  /* 每个 Agent 要去的位置。空闲的人记住上次去的休息位，避免每次轮询都换地方 */
  const prefsRef = useRef(new Map<string, string>());
  const [wander, setWander] = useState(0);
  const targets = useMemo(() => {
    const result = new Map<string, string>();
    if (!layout) return result;
    const taken = new Set<string>();
    const idle: AgentStatus[] = [];
    sorted.forEach((agent) => {
      const desk = deskOf.get(agent.agent_id);
      const pose = agentPose(agent);
      if (desk === undefined || pose === 'idle') {
        idle.push(agent);
        return;
      }
      const spot = pose === 'waiting' ? `stand-${desk}` : `seat-${desk}`;
      result.set(agent.agent_id, spot);
      taken.add(`seat-${desk}`);
      taken.add(`stand-${desk}`);
    });
    const pending: AgentStatus[] = [];
    idle.forEach((agent) => {
      const pref = prefsRef.current.get(agent.agent_id);
      const desk = deskOf.get(agent.agent_id);
      const ownSeat = desk === undefined ? undefined : `seat-${desk}`;
      const prefSpot = pref ? layout.spots.get(pref) : undefined;
      if (pref && prefSpot && (pref === ownSeat || prefSpot.kind === 'lounge') && !taken.has(pref)) {
        result.set(agent.agent_id, pref);
        taken.add(pref);
      } else pending.push(agent);
    });
    pending.forEach((agent) => {
      const lounge = layout.loungeKeys.find((key) => !taken.has(key));
      const desk = deskOf.get(agent.agent_id);
      const ownSeat = desk !== undefined && !taken.has(`seat-${desk}`) ? `seat-${desk}` : undefined;
      const spot = lounge || ownSeat;
      if (!spot) return;
      result.set(agent.agent_id, spot);
      taken.add(spot);
    });
    return result;
    // wander 变化表示闲逛改了 prefsRef，需要重新分配
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sorted, layout, deskOf, wander]);

  useEffect(() => {
    // 只记住空闲 Agent 的去处；有任务的人下次空闲时重新找位置
    const next = new Map<string, string>();
    sorted.forEach((agent) => {
      const spot = targets.get(agent.agent_id);
      if (agentPose(agent) === 'idle' && spot) next.set(agent.agent_id, spot);
    });
    prefsRef.current = next;
  }, [targets, sorted]);

  // 闲逛：每隔一会儿让一个空闲 Agent 换个地方（偶尔回自己工位坐坐）。
  // 轮询每 3 秒刷新一次数据，所以定时器只建一次，从 ref 里读最新数据，否则会被不断重置永远触发不了
  const latest = useRef({ layout, sorted, targets, deskOf });
  latest.current = { layout, sorted, targets, deskOf };
  useEffect(() => {
    if (prefersReducedMotion()) return;
    const timer = window.setInterval(() => {
      const { layout: current, sorted: list, targets: assigned, deskOf: desks } = latest.current;
      if (!current || document.hidden) return;
      const idle = list.filter((agent) => agentPose(agent) === 'idle');
      if (!idle.length) return;
      const agent = idle[Math.floor(Math.random() * idle.length)];
      const taken = new Set(assigned.values());
      const options = current.loungeKeys.filter((key) => !taken.has(key));
      const deskIndex = desks.get(agent.agent_id);
      const ownSeat = deskIndex === undefined ? undefined : `seat-${deskIndex}`;
      if (ownSeat && !taken.has(ownSeat) && assigned.get(agent.agent_id) !== ownSeat && Math.random() < 0.2)
        options.push(ownSeat);
      if (!options.length) return;
      prefsRef.current.set(agent.agent_id, options[Math.floor(Math.random() * options.length)]);
      setWander((n) => n + 1);
    }, WANDER_INTERVAL);
    return () => window.clearInterval(timer);
  }, []);

  // 哪些 Agent 已经在座位上坐下（椅子由角色自己画）
  const [resting, setResting] = useState<Record<string, string | null>>({});
  const handleRest = React.useCallback((agentId: string, spotKey: string | null) => {
    setResting((prev) => (prev[agentId] === spotKey ? prev : { ...prev, [agentId]: spotKey }));
  }, []);

  const counts = { working: 0, waiting: 0, error: 0, rest: 0 };
  sorted.forEach((agent) => {
    const pose = agentPose(agent);
    if (pose === 'working') counts.working += 1;
    else if (pose === 'waiting') counts.waiting += 1;
    else if (pose === 'error') counts.error += 1;
    else counts.rest += 1;
  });
  const feed = [...sorted].sort((a, b) => FEED_ORDER[agentPose(a)] - FEED_ORDER[agentPose(b)]);
  const feedStatus = (agent: AgentStatus) => {
    const pose = agentPose(agent);
    if (pose === 'working') return `${getStatusLabel(agent.current_stage || agent.status)} · ${agent.progress}%`;
    const spot = layout?.spots.get(targets.get(agent.agent_id) || '');
    if (spot?.kind === 'lounge') return spot.activity;
    return pose === 'idle' ? '工位待命' : getStatusLabel(agent.status);
  };
  const actorOrder = layout
    ? [...sorted].sort(
        (a, b) => (layout.spots.get(targets.get(a.agent_id)!)?.y ?? 0) - (layout.spots.get(targets.get(b.agent_id)!)?.y ?? 0),
      )
    : [];

  return (
    <div className="office-layout">
      <div className="office-scene" ref={wrapRef}>
        {layout && (
          <svg
            className="office-svg"
            viewBox={`0 0 ${layout.width} ${layout.height}`}
            width="100%"
            role="img"
            aria-label="Agent 办公室平面图"
          >
            <defs>
              <pattern id="office-tiles" width="28" height="28" patternUnits="userSpaceOnUse">
                <path className="office-tile-line" d="M28 0 H0 V28" />
              </pattern>
            </defs>
            <rect className="office-floor-fill" width={layout.width} height={layout.height} />
            <rect width={layout.width} height={layout.height} fill="url(#office-tiles)" />
            {layout.zones.map((zone) => (
              <ZoneArt key={zone.zone} zone={zone} />
            ))}
            {layout.walls.map((w, i) => (
              <line key={i} className="office-wall-line" x1={w.x1} y1={w.y1} x2={w.x2} y2={w.y2} />
            ))}
            <Pill
              x={layout.desks[0].x + (textWidth('💻 修复工区', 10) + 14) / 2}
              y={22}
              text="💻 修复工区"
              className="is-room"
            />
            {layout.desks.map((desk, index) => {
              const owner = sorted.find((agent) => deskOf.get(agent.agent_id) === index);
              const target = owner ? targets.get(owner.agent_id) : undefined;
              const spot = target ? layout.spots.get(target) : undefined;
              const atDesk = !!owner && (target === `seat-${index}` || target === `stand-${index}`);
              return (
                <DeskArt
                  key={owner?.agent_id || `empty-${index}`}
                  desk={desk}
                  owner={owner}
                  look={owner ? resolveAgentPersona(owner.name).look : undefined}
                  atDesk={atDesk}
                  seated={!!owner && resting[owner.agent_id] === `seat-${index}`}
                  awayActivity={spot?.activity}
                  dimmed={!!owner && !isHighlighted(owner)}
                  selected={!!owner && selectedId === owner.agent_id}
                  onSelect={onSelect}
                  onReview={onReview}
                />
              );
            })}
            {actorOrder.map((agent) => (
              <Actor
                key={agent.agent_id}
                agent={agent}
                look={resolveAgentPersona(agent.name).look}
                target={targets.get(agent.agent_id)!}
                layout={layout}
                dimmed={!isHighlighted(agent)}
                selected={selectedId === agent.agent_id}
                onSelect={onSelect}
                onRest={handleRest}
              />
            ))}
          </svg>
        )}
      </div>

      <aside className="office-panel" aria-label="办公室动态">
        <div className="office-stats">
          <div className="office-stat stat-working">
            <span>作战中</span>
            <strong>{counts.working}</strong>
          </div>
          <div className="office-stat stat-waiting">
            <span>待你拍板</span>
            <strong>{counts.waiting}</strong>
          </div>
          <div className="office-stat stat-error">
            <span>异常踩坑</span>
            <strong>{counts.error}</strong>
          </div>
          <div className="office-stat stat-rest">
            <span>休息待命</span>
            <strong>{counts.rest}</strong>
          </div>
        </div>
        <h2 className="office-panel-title">工位动态</h2>
        {feed.length ? (
          <ul className="office-feed">
            {feed.map((agent) => {
              const pose = agentPose(agent);
              return (
                <li key={agent.agent_id}>
                  <button
                    type="button"
                    className={`office-feed-item pose-${pose} ${isHighlighted(agent) ? '' : 'is-dimmed'} ${selectedId === agent.agent_id ? 'is-selected' : ''}`}
                    onClick={() => onSelect(agent.agent_id)}
                  >
                    <AgentAvatar name={agent.name} status={agent.status} stage={agent.current_stage} size="sm" />
                    <span className="office-feed-body">
                      <span className="office-feed-top">
                        <strong>{agent.name}</strong>
                        <span className={`office-feed-status pose-${pose}`}>{feedStatus(agent)}</span>
                      </span>
                      <span className="office-feed-bug">{agent.current_bug?.title || '暂无任务'}</span>
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        ) : (
          <p className="office-feed-empty">办公室还没有 Agent，去 Bug 悬赏榜派发任务后就会有人入驻。</p>
        )}
      </aside>
    </div>
  );
};