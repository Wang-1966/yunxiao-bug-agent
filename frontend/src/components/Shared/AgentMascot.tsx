import React from 'react';
import type { AgentLook } from '../../utils/agentLooks';

/**
 * 全站统一的 Agent 形象：Q 版大头身（头大身小、大眼睛 + 高光 + 腮红）。
 * 物种可选，所有物种共用同一套骨架、表情、围巾和配饰，造型分配见 utils/agentLooks。
 * 坐标约定：脚底中心为 (0, 0)，向上为负。站立高约 56。
 */

export type MascotSpecies = 'cat' | 'bear' | 'penguin' | 'robot';
export type MascotView = 'front' | 'back';
export type MascotFace = 'normal' | 'happy' | 'dizzy' | 'sleepy';
export type MascotArms = 'down' | 'up' | 'cup' | 'type';

/** 当前全站使用的物种 */
export const MASCOT_SPECIES: MascotSpecies = 'robot';

export interface AgentMascotProps {
  look: AgentLook;
  species?: MascotSpecies;
  view?: MascotView;
  face?: MascotFace;
  arms?: MascotArms;
  /** 正面坐姿（沙发 / 懒人沙发）：脚往前伸 */
  sitting?: boolean;
  walking?: boolean;
  className?: string;
}

interface Palette {
  fur: string;
  belly: string;
  line: string;
  inner?: string;
  muzzle?: string;
  feet?: string;
}

const PALETTES: Record<MascotSpecies, Palette> = {
  cat: { fur: '#F6A65A', belly: '#FFF4E6', line: '#7A4520', inner: '#FFB7A8', muzzle: '#FFF4E6' },
  bear: { fur: '#C99266', belly: '#F4DEC4', line: '#5E3C24', inner: '#F0C9A6', muzzle: '#F4DEC4' },
  penguin: { fur: '#33507A', belly: '#FFFFFF', line: '#1C2C45', feet: '#FFA53D' },
  robot: { fur: '#E7EDF5', belly: '#C9D6E6', line: '#5A6B82', feet: '#9AA9BC' },
};

const SW = 1.6; // 统一描边粗细

/* ---------- 部件 ---------- */

function Ears({ species, p, view }: { species: MascotSpecies; p: Palette; view: MascotView }) {
  if (species === 'cat')
    return (
      <g stroke={p.line} strokeWidth={SW} strokeLinejoin="round">
        <path d="M-18 -42 L-14 -56 L-5 -48 Z" fill={p.fur} />
        <path d="M18 -42 L14 -56 L5 -48 Z" fill={p.fur} />
        {view === 'front' && (
          <g stroke="none" fill={p.inner}>
            <path d="M-15.6 -45 L-13.6 -52.6 L-8.6 -48 Z" />
            <path d="M15.6 -45 L13.6 -52.6 L8.6 -48 Z" />
          </g>
        )}
      </g>
    );
  if (species === 'bear')
    return (
      <g stroke={p.line} strokeWidth={SW}>
        <circle cx="-14" cy="-49" r="6" fill={p.fur} />
        <circle cx="14" cy="-49" r="6" fill={p.fur} />
        {view === 'front' && (
          <g stroke="none" fill={p.inner}>
            <circle cx="-14" cy="-49" r="3.2" />
            <circle cx="14" cy="-49" r="3.2" />
          </g>
        )}
      </g>
    );
  if (species === 'robot')
    return (
      <g stroke={p.line} strokeWidth={SW}>
        <rect x="-23" y="-41" width="5" height="10" rx="2" fill={p.feet} />
        <rect x="18" y="-41" width="5" height="10" rx="2" fill={p.feet} />
      </g>
    );
  return null;
}

/** 头顶装饰（画在帽子之后，帽子盖不住）：机器人天线、企鹅呆毛 */
function Topper({ species, p, look }: { species: MascotSpecies; p: Palette; look: AgentLook }) {
  if (species === 'robot')
    return (
      <g>
        <path d="M0 -51 V-57" stroke={p.line} strokeWidth={SW} />
        <circle cx="0" cy="-58.5" r="2.8" fill={look.color} stroke={p.line} strokeWidth={1.2} className="mascot-bulb" />
      </g>
    );
  if (species === 'penguin')
    return <path d="M-1 -51 Q2 -57 5 -55" fill="none" stroke={p.fur} strokeWidth={2.4} strokeLinecap="round" />;
  return null;
}

function Head({ species, p, view }: { species: MascotSpecies; p: Palette; view: MascotView }) {
  if (species === 'robot')
    return (
      <g>
        <rect x="-19" y="-51" width="38" height="31" rx="11" fill={p.fur} stroke={p.line} strokeWidth={SW} />
        {view === 'front' && <rect x="-14" y="-45" width="28" height="18" rx="7" fill="#1E293B" />}
      </g>
    );
  return (
    <g>
      <ellipse cx="0" cy="-35" rx="20" ry="16.5" fill={p.fur} stroke={p.line} strokeWidth={SW} />
      {view === 'front' && species === 'penguin' && (
        <path d="M0 -42 C-4 -48 -15 -46 -15 -36 C-15 -27 -8 -22 0 -22 C8 -22 15 -27 15 -36 C15 -46 4 -48 0 -42 Z" fill={p.belly} />
      )}
      {view === 'front' && species === 'cat' && (
        <path d="M-3 -51 L-3 -46 M0 -51.5 L0 -45.5 M3 -51 L3 -46" stroke="#D9833A" strokeWidth={1.6} strokeLinecap="round" />
      )}
      {view === 'front' && species !== 'penguin' && <ellipse cx="0" cy="-27" rx="7.5" ry="5.2" fill={p.muzzle} />}
    </g>
  );
}

function Eyes({ species, face }: { species: MascotSpecies; face: MascotFace }) {
  if (species === 'robot') {
    const glow = { fill: 'none', stroke: '#5EEAD4', strokeWidth: 2.4, strokeLinecap: 'round' as const };
    if (face === 'happy') return <path d="M-9 -34 Q-6 -38 -3 -34 M3 -34 Q6 -38 9 -34" {...glow} />;
    if (face === 'sleepy') return <path d="M-9 -36 H-3 M3 -36 H9" {...glow} />;
    if (face === 'dizzy') return <path d="M-9 -39 L-3 -33 M-3 -39 L-9 -33 M3 -39 L9 -33 M9 -39 L3 -33" {...glow} stroke="#F87171" />;
    return (
      <g fill="#5EEAD4" className="mascot-visor-eyes">
        <rect x="-9" y="-40" width="5" height="8" rx="2.5" />
        <rect x="4" y="-40" width="5" height="8" rx="2.5" />
      </g>
    );
  }
  const line = { fill: 'none', stroke: '#3B2418', strokeWidth: 1.9, strokeLinecap: 'round' as const };
  if (face === 'happy') return <path d="M-11 -34 Q-7.5 -38.5 -4 -34 M4 -34 Q7.5 -38.5 11 -34" {...line} />;
  if (face === 'sleepy') return <path d="M-11 -35 Q-7.5 -32 -4 -35 M4 -35 Q7.5 -32 11 -35" {...line} />;
  if (face === 'dizzy') return <path d="M-10.5 -38 L-4.5 -32 M-4.5 -38 L-10.5 -32 M4.5 -38 L10.5 -32 M10.5 -38 L4.5 -32" {...line} />;
  return (
    <g>
      <ellipse cx="-7.5" cy="-35" rx="3.6" ry="4.4" fill="#3B2418" />
      <ellipse cx="7.5" cy="-35" rx="3.6" ry="4.4" fill="#3B2418" />
      <circle cx="-6.2" cy="-36.6" r="1.5" fill="#FFFFFF" />
      <circle cx="8.8" cy="-36.6" r="1.5" fill="#FFFFFF" />
      <circle cx="-8.6" cy="-33.4" r="0.7" fill="#FFFFFF" />
      <circle cx="6.4" cy="-33.4" r="0.7" fill="#FFFFFF" />
    </g>
  );
}

function Mouth({ species, face }: { species: MascotSpecies; face: MascotFace }) {
  if (species === 'robot') return face === 'happy' ? <path d="M-3 -24 Q0 -21.5 3 -24" fill="none" stroke={PALETTES.robot.line} strokeWidth={1.4} strokeLinecap="round" /> : null;
  if (species === 'penguin') return <path d="M-3.4 -29 L3.4 -29 L0 -25 Z" fill="#FFA53D" stroke="#C26A12" strokeWidth={1} strokeLinejoin="round" />;
  const nose = species === 'cat' ? <path d="M-1.8 -29.4 H1.8 L0 -27.6 Z" fill="#E7756A" /> : <ellipse cx="0" cy="-29" rx="2.6" ry="1.8" fill="#4A2C1A" />;
  return (
    <g>
      {nose}
      <path
        d={face === 'dizzy' ? 'M-2.6 -24.4 Q0 -26 2.6 -24.4' : 'M-3.4 -26 Q-1.7 -24 0 -26 Q1.7 -24 3.4 -26'}
        fill="none"
        stroke="#3B2418"
        strokeWidth={1.3}
        strokeLinecap="round"
      />
    </g>
  );
}

function Scarf({ look, view }: { look: AgentLook; view: MascotView }) {
  const dark = { stroke: 'rgba(0,0,0,0.18)', strokeWidth: 1 };
  if (view === 'back')
    return (
      <g fill={look.color} {...dark}>
        <path d="M-15 -21.5 Q0 -17 15 -21.5 L15 -16.5 Q0 -12 -15 -16.5 Z" />
        <path d="M-5 -18 L-8 -6 L-2.4 -7 L-1 -17 Z" />
      </g>
    );
  return (
    <g fill={look.color} {...dark}>
      <path d="M-15 -21.5 Q0 -16 15 -21.5 L15 -16.5 Q0 -11 -15 -16.5 Z" />
      <path d="M6 -15.5 L10.5 -5 L5 -5.6 L3 -14.5 Z" />
    </g>
  );
}

function Accessory({ species, look, view }: { species: MascotSpecies; look: AgentLook; view: MascotView }) {
  if (look.accessory === 'glasses' && view === 'front' && species !== 'robot')
    return (
      <g fill="rgba(255,255,255,0.25)" stroke={look.color} strokeWidth={1.7}>
        <circle cx="-7.5" cy="-35" r="6" />
        <circle cx="7.5" cy="-35" r="6" />
        <path d="M-1.5 -35.5 Q0 -36.5 1.5 -35.5" fill="none" />
      </g>
    );
  if (look.accessory === 'glasses' && species === 'robot' && view === 'front')
    return <rect x="-14" y="-45" width="28" height="18" rx="7" fill="none" stroke={look.color} strokeWidth={1.8} />;
  if (look.accessory === 'headphones')
    return (
      <g>
        <path d="M-19 -36 C-19 -58 19 -58 19 -36" fill="none" stroke={look.color} strokeWidth={3.2} strokeLinecap="round" />
        <rect x="-24" y="-41" width="7.5" height="12" rx="3.4" fill={look.color} stroke="rgba(0,0,0,0.2)" strokeWidth={1} />
        <rect x="16.5" y="-41" width="7.5" height="12" rx="3.4" fill={look.color} stroke="rgba(0,0,0,0.2)" strokeWidth={1} />
      </g>
    );
  if (look.accessory === 'hardhat')
    return (
      <g stroke="#B7791F" strokeWidth={1.2}>
        <path d="M-15 -45 C-15 -58 15 -58 15 -45 Z" fill="#FBBF24" />
        <rect x="-19" y="-47" width="38" height="4" rx="2" fill="#F59E0B" />
      </g>
    );
  return null;
}

function Arm({ cx, cy, angle, p, species }: { cx: number; cy: number; angle: number; p: Palette; species: MascotSpecies }) {
  const fill = species === 'robot' ? p.feet : p.fur;
  return <ellipse cx={cx} cy={cy} rx="3.4" ry="5.6" transform={`rotate(${angle} ${cx} ${cy})`} fill={fill} stroke={p.line} strokeWidth={SW} />;
}

function Arms({ arms, view, p, species }: { arms: MascotArms; view: MascotView; p: Palette; species: MascotSpecies }) {
  const a = { p, species };
  if (arms === 'up')
    return (
      <g>
        <Arm cx={-18} cy={-25} angle={-32} {...a} />
        <Arm cx={18} cy={-25} angle={32} {...a} />
      </g>
    );
  if (arms === 'type')
    return (
      <g>
        {/* 动画放在外层 g 上：CSS transform 会覆盖元素自身的 transform 属性 */}
        <g className="mascot-type-l"><Arm cx={-11} cy={-14} angle={-150} {...a} /></g>
        <g className="mascot-type-r"><Arm cx={11} cy={-14} angle={150} {...a} /></g>
      </g>
    );
  return (
    <g>
      <Arm cx={-13} cy={-10} angle={20} {...a} />
      {arms === 'cup' && view === 'front' ? (
        <g>
          <Arm cx={12} cy={-13} angle={-70} {...a} />
          <rect className="mascot-cup" x="13" y="-21" width="8" height="9" rx="1.6" />
          <path className="mascot-steam" d="M16 -23 q-1.5 -3 0.5 -6 M19 -23 q-1.5 -3 0.5 -6" />
        </g>
      ) : (
        <Arm cx={13} cy={-10} angle={-20} {...a} />
      )}
    </g>
  );
}

/** Agent 形象本体。脚底中心为原点，用 transform 放到场景里。 */
export const AgentMascot: React.FC<AgentMascotProps> = ({
  look,
  species = MASCOT_SPECIES,
  view = 'front',
  face = 'normal',
  arms = 'down',
  sitting = false,
  walking = false,
  className = '',
}) => {
  const p = PALETTES[species];
  const feetFill = p.feet || p.fur;
  return (
    <g className={`agent-mascot species-${species} ${walking ? 'is-walking' : ''} ${className}`}>
      {/* 腿 / 脚 */}
      {sitting ? (
        <g fill={feetFill} stroke={p.line} strokeWidth={SW}>
          <ellipse cx="-7" cy="-2" rx="5" ry="3.6" />
          <ellipse cx="7" cy="-2" rx="5" ry="3.6" />
        </g>
      ) : (
        <g fill={feetFill} stroke={p.line} strokeWidth={SW}>
          <ellipse className="mascot-leg-l" cx="-6.5" cy="-2.6" rx="5" ry="3.2" />
          <ellipse className="mascot-leg-r" cx="6.5" cy="-2.6" rx="5" ry="3.2" />
        </g>
      )}
      <g className="mascot-upper">
        {species === 'cat' && (
          <path
            d={view === 'back' ? 'M8 -8 Q22 -10 20 -24 Q19 -30 23 -32' : 'M10 -6 Q24 -8 22 -20'}
            fill="none"
            stroke={p.line}
            strokeWidth={6}
            strokeLinecap="round"
          />
        )}
        {species === 'cat' && (
          <path
            d={view === 'back' ? 'M8 -8 Q22 -10 20 -24 Q19 -30 23 -32' : 'M10 -6 Q24 -8 22 -20'}
            fill="none"
            stroke={p.fur}
            strokeWidth={3.4}
            strokeLinecap="round"
          />
        )}
        {/* 身体 */}
        <path d="M-13 -20 C-15 -10 -14 -4 -9 -3.5 H9 C14 -4 15 -10 13 -20 Z" fill={p.fur} stroke={p.line} strokeWidth={SW} strokeLinejoin="round" />
        {view === 'front' && <ellipse cx="0" cy="-11" rx="7.5" ry="6.5" fill={p.belly} />}
        {view === 'front' && species === 'robot' && <circle cx="0" cy="-11" r="2.6" fill={look.color} className="mascot-bulb" />}
        {view === 'back' && species === 'bear' && <circle cx="0" cy="-6" r="3" fill={p.belly} stroke={p.line} strokeWidth={1.2} />}
        <Ears species={species} p={p} view={view} />
        <Head species={species} p={p} view={view} />
        {view === 'front' && (
          <g>
            <Eyes species={species} face={face} />
            {species !== 'robot' && (
              <g fill="#FF8A8A" opacity="0.45">
                <ellipse cx="-13" cy="-28.5" rx="3.4" ry="2" />
                <ellipse cx="13" cy="-28.5" rx="3.4" ry="2" />
              </g>
            )}
            <Mouth species={species} face={face} />
          </g>
        )}
        {/* 头顶高光，让形象更立体 */}
        {species !== 'robot' && <ellipse cx="-8" cy="-45" rx="5" ry="2.4" fill="#FFFFFF" opacity="0.35" transform="rotate(-18 -8 -45)" />}
        <Scarf look={look} view={view} />
        <Accessory species={species} look={look} view={view} />
        <Topper species={species} p={p} look={look} />
        <Arms arms={arms} view={view} p={p} species={species} />
      </g>
    </g>
  );
};

/** 办公椅（背面视角，原点 = 座位处角色脚底） */
export const OfficeChair: React.FC<{ className?: string }> = ({ className = '' }) => (
  <g className={`office-chair-group ${className}`}>
    <rect className="office-chair-stem" x="-2" y="1" width="4" height="7" />
    <path className="office-chair-base" d="M-14 9.5 H14" />
    <circle className="office-chair-wheel" cx="-14" cy="10.5" r="2.2" />
    <circle className="office-chair-wheel" cx="0" cy="10.5" r="2.2" />
    <circle className="office-chair-wheel" cx="14" cy="10.5" r="2.2" />
    <rect className="office-chair-back" x="-18" y="-15" width="36" height="17" rx="6" />
  </g>
);