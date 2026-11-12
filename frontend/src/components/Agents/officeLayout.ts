/**
 * 办公室场景布局 + 走路寻路。整个办公室是一个 SVG 坐标系（单位 = px）。
 *
 * 每个可停留的位置（工位座位、工位旁站位、休息区位置）称为 spot。
 * 每排工位前、每个休息区前都有一条横向过道（lane），过道在 spine（竖向走廊）上相连。
 * Agent 走路路线：spot → 所在过道 → spine → 目标过道 → 目标 spot，全部是横平竖直的线段。
 */

export interface Point {
  x: number;
  y: number;
}

export type Zone = 'pantry' | 'sofa' | 'gym';
export type LoungePose = 'cup' | 'chat' | 'brew' | 'sofa' | 'run' | 'beanbag' | 'stretch';

export interface Spot extends Point {
  key: string;
  kind: 'seat' | 'stand' | 'lounge';
  laneY: number;
  pose?: LoungePose;
  activity?: string;
  zone?: Zone;
  /** 名牌相对头顶的偏移，避免挤在一起 */
  labelDy?: number;
}

export interface ZoneBox extends Point {
  zone: Zone;
  w: number;
  h: number;
  title: string;
}

export interface Wall {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
}

export interface OfficeLayout {
  width: number;
  height: number;
  desks: Point[];
  zones: ZoneBox[];
  walls: Wall[];
  spots: Map<string, Spot>;
  loungeKeys: string[];
  /** 布局签名：变化时角色瞬移到新位置，而不是走过去 */
  signature: string;
  graph: Graph;
}

/* 工位格子：原点在格子左上角 */
export const DESK = {
  w: 150,
  h: 150,
  seat: { x: 75, y: 112 },
  stand: { x: 128, y: 118 },
  laneY: 146,
} as const;
export const ZONE = { w: 260, h: 150, laneY: 136 } as const;

const GAP_X = 14;
const PAD = 16;
const SPINE_HALF = 22;

const LOUNGE_LOCAL: Record<Zone, Omit<Spot, 'laneY' | 'zone' | 'kind'>[]> = {
  pantry: [
    { key: 'pantry-0', x: 46, y: 118, pose: 'cup', activity: '在茶水间续咖啡' },
    { key: 'pantry-1', x: 124, y: 118, pose: 'chat', activity: '在茶水间聊天' },
    { key: 'pantry-2', x: 206, y: 112, pose: 'brew', activity: '在研究咖啡机' },
  ],
  sofa: [
    { key: 'sofa-0', x: 82, y: 104, pose: 'sofa', activity: '窝在沙发上回血' },
    // 中间座位名牌放到脚下的地毯上，避免和两边名牌挤在一起
    { key: 'sofa-1', x: 136, y: 104, pose: 'sofa', activity: '窝在沙发上回血', labelDy: 22 },
    { key: 'sofa-2', x: 190, y: 104, pose: 'sofa', activity: '窝在沙发上回血' },
  ],
  gym: [
    { key: 'gym-0', x: 58, y: 112, pose: 'run', activity: '在跑步机上放空' },
    { key: 'gym-1', x: 214, y: 108, pose: 'beanbag', activity: '瘫在懒人沙发里' },
    { key: 'gym-2', x: 138, y: 118, pose: 'stretch', activity: '在做拉伸' },
  ],
};

/** 空闲 Agent 入座顺序：先把三个区域各坐一个人，再往里加 */
export const LOUNGE_ORDER = [
  'sofa-0', 'pantry-0', 'gym-0', 'sofa-2', 'pantry-1', 'gym-1', 'sofa-1', 'gym-2', 'pantry-2',
];

const ZONE_TITLES: Record<Zone, string> = { pantry: '☕ 茶水间', sofa: '🛋️ 休息区', gym: '🏃 健身角' };
const ZONE_ORDER: Zone[] = ['pantry', 'sofa', 'gym'];

export interface PathPoint extends Point {
  node: string;
}

interface GraphNode extends Point {
  edges: string[];
}
export type Graph = Map<string, GraphNode>;

function addNode(graph: Graph, key: string, p: Point) {
  if (!graph.has(key)) graph.set(key, { x: p.x, y: p.y, edges: [] });
}
function link(graph: Graph, a: string, b: string) {
  graph.get(a)!.edges.push(b);
  graph.get(b)!.edges.push(a);
}

/**
 * 计算办公室布局。
 * - 宽屏：左侧一列休息区，竖向走廊，右侧工位网格
 * - 窄屏：工位在上，横向走廊，休息区在下（能并排就并排）
 * - 工位数量固定上限 6 个（宽屏 2 行 × 3 列，窄屏 3 行 × 2 列），超过的 Agent 不分配工位
 */
export function buildOfficeLayout(containerWidth: number, agentCount: number): OfficeLayout {
  const width = Math.max(320, Math.floor(containerWidth));
  const loungeW = ZONE.w;
  const wideCols = Math.floor((width - PAD - loungeW - SPINE_HALF * 2 - PAD + GAP_X) / (DESK.w + GAP_X));
  const wide = wideCols >= 2;
  const MAX_DESKS = 6;
  const cols = wide ? 3 : 2;
  const rows = wide ? 2 : 3;
  const deskCount = Math.min(MAX_DESKS, Math.max(agentCount, cols * rows));
  const deskBlockW = cols * DESK.w + (cols - 1) * GAP_X;

  const desks: Point[] = [];
  const zones: ZoneBox[] = [];
  const walls: Wall[] = [];
  let sceneW: number;
  let sceneH: number;
  let spineX: number;

  if (wide) {
    spineX = PAD + loungeW + SPINE_HALF;
    // 列数取整后剩下的宽度平分到两边，避免工位全挤在左边
    const spare = Math.max(0, width - PAD - (spineX + SPINE_HALF) - deskBlockW);
    const deskX0 = spineX + SPINE_HALF + Math.floor(spare / 2);
    const deskY0 = 34;
    for (let i = 0; i < deskCount; i++)
      desks.push({ x: deskX0 + (i % cols) * (DESK.w + GAP_X), y: deskY0 + Math.floor(i / cols) * DESK.h });
    ZONE_ORDER.forEach((zone, i) =>
      zones.push({ zone, x: PAD, y: PAD + i * (ZONE.h + 6), w: ZONE.w, h: ZONE.h, title: ZONE_TITLES[zone] }),
    );
    const loungeBottom = PAD + ZONE_ORDER.length * (ZONE.h + 6) - 6;
    sceneW = Math.max(width, deskX0 + deskBlockW + PAD);
    sceneH = Math.max(loungeBottom, deskY0 + rows * DESK.h) + PAD;
    // 休息区和走廊之间的墙，每个区域的过道处留门
    const wallX = PAD + loungeW + 4;
    let y = PAD;
    zones.forEach((z) => {
      const door = z.y + ZONE.laneY;
      walls.push({ x1: wallX, y1: y, x2: wallX, y2: door - 16 });
      y = door + 14;
    });
    walls.push({ x1: wallX, y1: y, x2: wallX, y2: loungeBottom });
  } else {
    const deskX0 = Math.max(PAD, Math.floor((width - deskBlockW) / 2));
    spineX = deskX0 - 8;
    const deskY0 = 34;
    for (let i = 0; i < deskCount; i++)
      desks.push({ x: deskX0 + (i % cols) * (DESK.w + GAP_X), y: deskY0 + Math.floor(i / cols) * DESK.h });
    const loungeY = deskY0 + rows * DESK.h + 18;
    const perRow = Math.max(1, Math.min(3, Math.floor((width - PAD * 2 + 8) / (ZONE.w + 8))));
    const zonesW = perRow * ZONE.w + (perRow - 1) * 8;
    const zoneX0 = Math.max(PAD, Math.floor((width - zonesW) / 2));
    spineX = Math.min(spineX, zoneX0 - 8);
    ZONE_ORDER.forEach((zone, i) =>
      zones.push({
        zone,
        x: zoneX0 + (i % perRow) * (ZONE.w + 8),
        y: loungeY + Math.floor(i / perRow) * (ZONE.h + 6),
        w: ZONE.w,
        h: ZONE.h,
        title: ZONE_TITLES[zone],
      }),
    );
    sceneW = Math.max(width, deskX0 + deskBlockW + PAD, zoneX0 + zonesW + PAD);
    sceneH = loungeY + Math.ceil(ZONE_ORDER.length / perRow) * (ZONE.h + 6) + PAD;
    walls.push({ x1: spineX + 18, y1: loungeY - 9, x2: sceneW - PAD, y2: loungeY - 9 });
  }
  spineX = Math.max(8, spineX);

  /* 寻路图 */
  const graph: Graph = new Map();
  const spots = new Map<string, Spot>();
  const lanes = new Map<number, string[]>();
  const addSpot = (spot: Spot) => {
    spots.set(spot.key, spot);
    addNode(graph, spot.key, spot);
    const access = `${spot.key}@lane`;
    addNode(graph, access, { x: spot.x, y: spot.laneY });
    link(graph, spot.key, access);
    if (!lanes.has(spot.laneY)) lanes.set(spot.laneY, []);
    lanes.get(spot.laneY)!.push(access);
  };
  desks.forEach((d, i) => {
    const laneY = d.y + DESK.laneY;
    addSpot({ key: `seat-${i}`, kind: 'seat', x: d.x + DESK.seat.x, y: d.y + DESK.seat.y, laneY });
    addSpot({ key: `stand-${i}`, kind: 'stand', x: d.x + DESK.stand.x, y: d.y + DESK.stand.y, laneY });
  });
  zones.forEach((z) =>
    LOUNGE_LOCAL[z.zone].forEach((s) =>
      addSpot({ ...s, kind: 'lounge', zone: z.zone, x: z.x + s.x, y: z.y + s.y, laneY: z.y + ZONE.laneY }),
    ),
  );
  const spineNodes: string[] = [];
  lanes.forEach((nodes, laneY) => {
    const spine = `spine@${laneY}`;
    addNode(graph, spine, { x: spineX, y: laneY });
    spineNodes.push(spine);
    const ordered = [spine, ...nodes].sort((a, b) => graph.get(a)!.x - graph.get(b)!.x);
    for (let i = 1; i < ordered.length; i++) link(graph, ordered[i - 1], ordered[i]);
  });
  spineNodes.sort((a, b) => graph.get(a)!.y - graph.get(b)!.y);
  for (let i = 1; i < spineNodes.length; i++) link(graph, spineNodes[i - 1], spineNodes[i]);

  return {
    width: sceneW,
    height: sceneH,
    desks,
    zones,
    walls,
    spots,
    loungeKeys: LOUNGE_ORDER,
    signature: `${wide ? 'w' : 'n'}:${cols}:${rows}:${zones.map((z) => `${z.x},${z.y}`).join(';')}:${sceneW}`,
    graph,
  };
}

export interface DeskCandidate {
  agent_id: string;
  idle: boolean;
}

/**
 * 传入顺序（姓名序）的前 deskCount 人占住工位。
 * 排在后面但正在干活的人换掉最后一名空闲占座者，否则目标座位不在图上，人会停在休息区。
 * ponytail: 同时干活的人数超过工位数时，多出来的人没有工位。要再加人就提高上面的 MAX_DESKS。
 */
export function assignDesks(agents: DeskCandidate[], deskCount: number): Map<string, number> {
  const owners = agents.slice(0, Math.max(0, deskCount)).map((agent) => agent.agent_id);
  const isIdle = (id: string) => agents.find((agent) => agent.agent_id === id)?.idle;
  for (const agent of agents.slice(owners.length)) {
    if (agent.idle) continue;
    let slot = -1;
    for (let i = owners.length - 1; i >= 0; i--) {
      if (isIdle(owners[i])) {
        slot = i;
        break;
      }
    }
    if (slot < 0) break;
    owners[slot] = agent.agent_id;
  }
  return new Map(owners.map((id, index) => [id, index]));
}

/** 图上最短路（节点数 < 100，直接 O(n²) Dijkstra） */
export function findPath(graph: Graph, from: string, to: string): PathPoint[] {
  if (!graph.has(from) || !graph.has(to)) return [];
  const dist = new Map<string, number>([[from, 0]]);
  const prev = new Map<string, string>();
  const open = new Set(graph.keys());
  while (open.size) {
    let current: string | undefined;
    let best = Infinity;
    open.forEach((key) => {
      const d = dist.get(key) ?? Infinity;
      if (d < best) {
        best = d;
        current = key;
      }
    });
    if (!current || current === to) break;
    open.delete(current);
    const node = graph.get(current)!;
    for (const next of node.edges) {
      const target = graph.get(next)!;
      const d = best + Math.abs(target.x - node.x) + Math.abs(target.y - node.y);
      if (d < (dist.get(next) ?? Infinity)) {
        dist.set(next, d);
        prev.set(next, current);
      }
    }
  }
  if (from !== to && !prev.has(to)) return [];
  const path: PathPoint[] = [];
  for (let key: string | undefined = to; key; key = key === from ? undefined : prev.get(key)) {
    const n = graph.get(key)!;
    path.unshift({ x: n.x, y: n.y, node: key });
  }
  return path;
}

function checkAssignDesks() {
  const agents: DeskCandidate[] = [
    { agent_id: '爱', idle: true },
    { agent_id: '戴', idle: true },
    { agent_id: '断', idle: true },
    { agent_id: '咖', idle: true },
    { agent_id: '零', idle: true },
    { agent_id: '日', idle: true },
    { agent_id: '通', idle: true },
    { agent_id: '修B', idle: true },
    { agent_id: '修E', idle: false },
    { agent_id: '修F', idle: true },
    { agent_id: '夜', idle: true },
  ];
  const desks = assignDesks(agents, 6);
  if (desks.get('修E') !== 5 || desks.has('日') || desks.get('零') !== 4)
    throw new Error(`assignDesks ${JSON.stringify([...desks])}`);
  const full = assignDesks(agents.map((agent) => ({ ...agent, idle: false })), 6);
  if (full.has('修E') || full.get('日') !== 5) throw new Error('six busy agents keep the name-order desks');
  const layout = buildOfficeLayout(1200, agents.length);
  if (findPath(layout.graph, 'pantry-2', `stand-${desks.get('修E')}`).length < 2)
    throw new Error('no path from the pantry to the borrowed desk');
  console.log('assignDesks ok');
}

const entry = (globalThis as { process?: { argv?: string[] } }).process?.argv?.[1];
if (entry?.endsWith('officeLayout.ts')) checkAssignDesks();
