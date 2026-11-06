import React from 'react';
import { AgentMascot, type MascotFace } from './AgentMascot';
import { resolveAgentLook, type AgentLook } from '../../utils/agentLooks';

export interface AgentPersona {
  roleTitle: string;
  specialty: string;
  /** 与该 Agent 围巾颜色一致 */
  accentColor: string;
  accessoryLabel: string;
  look: AgentLook;
}

/** 名字只决定角色文案；外形由 resolveAgentLook 按围巾颜色 + 配饰分配 */
const ROLE_TEXT: Record<string, { roleTitle: string; specialty: string }> = {
  夜猫子修修: { roleTitle: '子夜堆栈侦探', specialty: '擅长在深夜复杂调用链里捕捉隐蔽异常' },
  咖啡因大夫: { roleTitle: '急诊代码主刀', specialty: '三杯浓缩下肚，专治各种疑难崩溃与内存泄漏' },
  通宵啄木鸟: { roleTitle: '深林源码猎手', specialty: '高频敲击语法树，把藏在深处的缺陷精准揪出' },
  爱较真的补丁匠: { roleTitle: '精密缝合大师', specialty: '拒绝敷衍糊弄，每一针代码补丁都严丝合缝' },
  戴眼镜的捉虫侠: { roleTitle: '全栈追踪游侠', specialty: '任何边界条件都逃不过这副眼镜' },
  零警告强迫症: { roleTitle: '洁癖守门员', specialty: '不仅修好 Bug，连顺手看到的边界隐患也绝不放过' },
  断点旅行者: { roleTitle: '时空回溯漫游者', specialty: '在执行帧与快照之间穿梭，定格问题发生瞬间' },
  日志考古家: { roleTitle: '远古报错破译员', specialty: '从陈年日志与提交历史中还原事故第一现场' },
};
const FALLBACK_ROLE = { roleTitle: '修复特工', specialty: '随时待命，接手新的 Bug 悬赏' };

export function resolveAgentPersona(name: string): AgentPersona {
  const clean = (name || '').trim();
  const role =
    Object.entries(ROLE_TEXT).find(([prefix]) => clean.startsWith(prefix))?.[1] || FALLBACK_ROLE;
  const look = resolveAgentLook(clean);
  const accessoryLabel =
    look.accessory === 'none'
      ? `${look.colorName}围巾`
      : `${look.colorName}围巾 · ${look.accessoryLabel}`;
  return { ...role, accentColor: look.color, accessoryLabel, look };
}

export function getAgentMood(status?: string, stage?: string): {
  emoji: string;
  bubbleText: string;
  moodClass: string;
} {
  if (status === 'error' || status === 'failed' || stage === 'failed')
    return { emoji: '💫', bubbleText: '踩到暗坑啦，随时可断点续跑！', moodClass: 'mood-error' };
  if (status === 'awaiting_plan_confirmation' || stage === 'awaiting_plan_confirmation')
    return { emoji: '📋', bubbleText: '修复蓝图已备好，等你拍板定案～', moodClass: 'mood-waiting' };
  if (status === 'awaiting_confirmation' || stage === 'awaiting_confirmation')
    return { emoji: '🏆', bubbleText: '自测全绿通关！快来验收成果吧', moodClass: 'mood-celebrate' };
  if (status === 'paused')
    return { emoji: '💤', bubbleText: '原地待命中，喝口热茶充充电…', moodClass: 'mood-paused' };
  if (status === 'busy' || status === 'queued') {
    switch (stage) {
      case 'queued':
        return { emoji: '🎒', bubbleText: '背好工具包，正在排队入场…', moodClass: 'mood-busy' };
      case 'analyzing':
        return { emoji: '🔎', bubbleText: '顺着源码蛛丝马迹深挖根因中…', moodClass: 'mood-busy' };
      case 'proposing':
        return { emoji: '💡', bubbleText: '正在头脑风暴最优修复方案…', moodClass: 'mood-busy' };
      case 'fixing':
        return { emoji: '🛠️', bubbleText: '无影手改码中，补丁精准缝合…', moodClass: 'mood-working' };
      case 'verifying':
      case 'testing':
        return { emoji: '🧪', bubbleText: '试炼场跑测试中，祈祷一路绿灯！', moodClass: 'mood-busy' };
      default:
        return { emoji: '⚡', bubbleText: '全神贯注攻克悬赏中…', moodClass: 'mood-busy' };
    }
  }
  return { emoji: '☕', bubbleText: '工位整洁待命，随时准备出击', moodClass: 'mood-idle' };
}

export function moodFace(status?: string): MascotFace {
  if (status === 'error' || status === 'failed') return 'dizzy';
  if (status === 'awaiting_confirmation' || status === 'completed') return 'happy';
  if (status === 'paused') return 'sleepy';
  return 'normal';
}

interface AgentAvatarProps {
  name: string;
  status?: string;
  stage?: string;
  size?: 'sm' | 'md' | 'lg';
  showBubble?: boolean;
}

export const AgentAvatar: React.FC<AgentAvatarProps> = ({
  name,
  status = 'idle',
  stage,
  size = 'md',
  showBubble = false,
}) => {
  const persona = resolveAgentPersona(name);
  const mood = getAgentMood(status, stage);
  const isWorking = status === 'busy';
  const isCelebrate = status === 'awaiting_confirmation' || status === 'completed';
  const isError = status === 'error' || status === 'failed';
  const dimensions = size === 'sm' ? 42 : size === 'lg' ? 68 : 54;

  return (
    <div className={`agent-avatar-wrap size-${size} ${mood.moodClass}`}>
      <div
        className={`agent-avatar-frame ${isWorking ? 'is-working' : ''} ${isCelebrate ? 'is-celebrate' : ''} ${isError ? 'is-error' : ''}`}
        style={{
          width: dimensions,
          height: dimensions,
          borderColor: persona.accentColor,
          ['--look' as string]: persona.accentColor,
        }}
        title={`${name} · ${persona.roleTitle}（${persona.accessoryLabel}）`}
      >
        {/* 头像只取上半身：天线到围巾 */}
        <svg viewBox="-27 -64 54 54" width="100%" height="100%" aria-hidden="true" className="agent-avatar-svg">
          <AgentMascot look={persona.look} face={moodFace(status)} />
        </svg>
        <span className="agent-mood-badge" title={mood.bubbleText}>
          {mood.emoji}
        </span>
      </div>
      {showBubble && (
        <div className="agent-speech-bubble">
          <span>{mood.bubbleText}</span>
        </div>
      )}
    </div>
  );
};
