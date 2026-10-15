import React from 'react';

export interface AgentPersona {
  key:
    | 'owl'
    | 'doctor'
    | 'woodpecker'
    | 'patcher'
    | 'bughunter'
    | 'perfectionist'
    | 'traveler'
    | 'archaeologist';
  roleTitle: string;
  specialty: string;
  bgGradient: string;
  accentColor: string;
  accessoryLabel: string;
}

const PERSONA_MAP: Record<string, AgentPersona> = {
  '夜猫子修修': {
    key: 'owl',
    roleTitle: '子夜堆栈侦探',
    specialty: '擅长在深夜复杂调用链里捕捉隐蔽异常',
    bgGradient: 'linear-gradient(135deg, #1E1B4B 0%, #312E81 100%)',
    accentColor: '#D97706',
    accessoryLabel: '🦉 夜视猫头鹰',
  },
  '咖啡因大夫': {
    key: 'doctor',
    roleTitle: '急诊代码主刀',
    specialty: '三杯浓缩下肚，专治各种疑难崩溃与内存泄漏',
    bgGradient: 'linear-gradient(135deg, #CCFBF1 0%, #99F6E4 100%)',
    accentColor: '#0D9488',
    accessoryLabel: '☕ 急诊外科主刀',
  },
  '通宵啄木鸟': {
    key: 'woodpecker',
    roleTitle: '深林源码猎手',
    specialty: '高频敲击语法树，把藏在深处的缺陷精准揪出',
    bgGradient: 'linear-gradient(135deg, #DCFCE7 0%, #86EFAC 100%)',
    accentColor: '#16A34A',
    accessoryLabel: '🐦 红冠啄木鸟',
  },
  '爱较真的补丁匠': {
    key: 'patcher',
    roleTitle: '精密缝合大师',
    specialty: '拒绝敷衍糊弄，每一针代码补丁都严丝合缝',
    bgGradient: 'linear-gradient(135deg, #FFEDD5 0%, #FDBA74 100%)',
    accentColor: '#EA580C',
    accessoryLabel: '🧵 单眼镜裁缝',
  },
  '戴眼镜的捉虫侠': {
    key: 'bughunter',
    roleTitle: '全栈追踪游侠',
    specialty: '厚底黑框镜片自带 AST 雷达，任何边界条件都逃不掉',
    bgGradient: 'linear-gradient(135deg, #E0E7FF 0%, #A5B4FC 100%)',
    accentColor: '#4F46E5',
    accessoryLabel: '👓 黑框极客游侠',
  },
  '零警告强迫症': {
    key: 'perfectionist',
    roleTitle: '洁癖守门员',
    specialty: '不仅修好 Bug，连顺手看到的边界隐患也绝不放过',
    bgGradient: 'linear-gradient(135deg, #ECFEFF 0%, #A5F3FC 100%)',
    accentColor: '#0891B2',
    accessoryLabel: '🛡️ 圣盾守门员',
  },
  '断点旅行者': {
    key: 'traveler',
    roleTitle: '时空回溯漫游者',
    specialty: '在无数个执行帧与快照之间穿梭，定格问题发生瞬间',
    bgGradient: 'linear-gradient(135deg, #2E1065 0%, #581C87 100%)',
    accentColor: '#9333EA',
    accessoryLabel: '🔴 时空漫游者',
  },
  '日志考古家': {
    key: 'archaeologist',
    roleTitle: '远古报错破译员',
    specialty: '从万行陈年日志与提交历史中还原事故第一现场',
    bgGradient: 'linear-gradient(135deg, #FEF3C7 0%, #FCD34D 100%)',
    accentColor: '#B45309',
    accessoryLabel: '🔍 探险考古家',
  },
};

const FALLBACK_PERSONAS: AgentPersona[] = Object.values(PERSONA_MAP);

export function resolveAgentPersona(name: string): AgentPersona {
  const cleanName = (name || '').trim();
  for (const [prefix, persona] of Object.entries(PERSONA_MAP)) {
    if (cleanName.startsWith(prefix)) {
      return persona;
    }
  }
  let hash = 0;
  for (let i = 0; i < cleanName.length; i++) {
    hash = (hash * 31 + cleanName.charCodeAt(i)) >>> 0;
  }
  return FALLBACK_PERSONAS[hash % FALLBACK_PERSONAS.length];
}

export function getAgentMood(status?: string, stage?: string): {
  emoji: string;
  bubbleText: string;
  moodClass: string;
} {
  if (status === 'error' || status === 'failed' || stage === 'failed') {
    return {
      emoji: '💫',
      bubbleText: '踩到暗坑啦，随时可断点续跑！',
      moodClass: 'mood-error',
    };
  }
  if (status === 'awaiting_plan_confirmation' || stage === 'awaiting_plan_confirmation') {
    return {
      emoji: '📋',
      bubbleText: '修复蓝图已备好，等你拍板定案～',
      moodClass: 'mood-waiting',
    };
  }
  if (status === 'awaiting_confirmation' || stage === 'awaiting_confirmation') {
    return {
      emoji: '🏆',
      bubbleText: '自测全绿通关！快来验收成果吧',
      moodClass: 'mood-celebrate',
    };
  }
  if (status === 'paused') {
    return {
      emoji: '💤',
      bubbleText: '原地待命中，喝口热茶充充电…',
      moodClass: 'mood-paused',
    };
  }
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
  return {
    emoji: '☕',
    bubbleText: '工位整洁待命，随时准备出击',
    moodClass: 'mood-idle',
  };
}

interface CharacterSvgProps {
  personaKey: AgentPersona['key'];
  isError: boolean;
  isCelebrate: boolean;
  isWorking: boolean;
}

const CharacterIllustration: React.FC<CharacterSvgProps> = ({
  personaKey,
  isError,
  isCelebrate,
  isWorking,
}) => {
  switch (personaKey) {
    case 'owl':
      // 夜猫子修修：真正的夜行猫头鹰造型（星空弯月 + 耳羽 + 琥珀大眼眶 + 金色菱形鸟喙）
      return (
        <g>
          {/* 夜空繁星与弯月 */}
          <circle cx="15" cy="16" r="1.3" fill="#FDE68A" />
          <circle cx="66" cy="22" r="1.1" fill="#FDE68A" />
          <path
            d="M62 10 A6 6 0 1 0 68 17 A4.5 4.5 0 1 1 62 10 Z"
            fill="#FBBF24"
          />
          {/* 猫头鹰身体与腹部羽毛纹理 */}
          <ellipse cx="40" cy="68" rx="24" ry="16" fill="#78350F" stroke="#1F1915" strokeWidth="2" />
          <ellipse cx="40" cy="70" rx="15" ry="12" fill="#FEF3C7" />
          <path d="M34 65 Q40 69 46 65 M32 71 Q40 75 48 71" stroke="#92400E" strokeWidth="1.6" fill="none" strokeLinecap="round" />
          {/* 猫头鹰圆头与翘起的耳羽 */}
          <path
            d="M14 14 L26 22 L19 32 Z"
            fill="#92400E"
            stroke="#1F1915"
            strokeWidth="2"
            strokeLinejoin="round"
          />
          <path
            d="M66 14 L54 22 L61 32 Z"
            fill="#92400E"
            stroke="#1F1915"
            strokeWidth="2"
            strokeLinejoin="round"
          />
          <ellipse
            cx="40"
            cy="39"
            rx="22"
            ry="20"
            fill="#B45309"
            stroke="#1F1915"
            strokeWidth="2.2"
          />
          {/* 面盘心形浅色羽毛 */}
          <circle cx="31" cy="38" r="11" fill="#FFFBEB" stroke="#78350F" strokeWidth="1.5" />
          <circle cx="49" cy="38" r="11" fill="#FFFBEB" stroke="#78350F" strokeWidth="1.5" />
          {/* 琥珀色大眼眶 */}
          <circle cx="31" cy="38" r="8" fill="#F59E0B" stroke="#1F1915" strokeWidth="2" />
          <circle cx="49" cy="38" r="8" fill="#F59E0B" stroke="#1F1915" strokeWidth="2" />
          {isError ? (
            <g stroke="#1F1915" strokeWidth="2.2" strokeLinecap="round">
              <path d="M28 35 L34 41 M34 35 L28 41" />
              <path d="M46 35 L52 41 M52 35 L46 41" />
            </g>
          ) : isCelebrate ? (
            <g stroke="#1F1915" strokeWidth="2.4" strokeLinecap="round" fill="none">
              <path d="M26 39 Q31 33 36 39" />
              <path d="M44 39 Q49 33 54 39" />
            </g>
          ) : (
            <g className={isWorking ? 'avatar-scan-eyes' : ''}>
              <circle cx="31" cy="38" r="4.2" fill="#1F1915" />
              <circle cx="49" cy="38" r="4.2" fill="#1F1915" />
              <circle cx="32.8" cy="36.2" r="1.6" fill="#FFFFFF" />
              <circle cx="50.8" cy="36.2" r="1.6" fill="#FFFFFF" />
            </g>
          )}
          {/* 金色菱形尖喙 */}
          <polygon
            points="40,41 35,47 40,54 45,47"
            fill="#FBBF24"
            stroke="#1F1915"
            strokeWidth="1.8"
            strokeLinejoin="round"
          />
        </g>
      );

    case 'woodpecker':
      // 通宵啄木鸟：鲜明红冠啄木鸟造型 + 右侧树干语法树 + 修长锋利金喙
      return (
        <g>
          {/* 右侧代码树干纹理 */}
          <rect x="64" y="6" width="12" height="68" rx="3" fill="#92400E" stroke="#1F1915" strokeWidth="1.8" />
          <line x1="68" y1="16" x2="68" y2="34" stroke="#78350F" strokeWidth="1.5" strokeLinecap="round" />
          <line x1="71" y1="42" x2="71" y2="62" stroke="#78350F" strokeWidth="1.5" strokeLinecap="round" />

          {/* 啄木鸟身体与羽翼 */}
          <path
            d="M16 76 C16 56, 48 56, 50 76 Z"
            fill="#1E293B"
            stroke="#1F1915"
            strokeWidth="2"
          />
          <path d="M24 63 Q33 70 42 63" fill="#FFFFFF" />

          <g className={isWorking ? 'avatar-peck' : ''}>
            {/* 标志性火红多羽冠 */}
            <path
              d="M34 26 C22 18, 12 18, 14 10 C22 8, 30 12, 36 16 C30 8, 24 4, 30 3 C40 4, 46 14, 44 24 Z"
              fill="#EF4444"
              stroke="#1F1915"
              strokeWidth="2"
              strokeLinejoin="round"
            />
            {/* 鸟头轮廓（黑白条纹羽毛） */}
            <ellipse
              cx="34"
              cy="39"
              rx="16"
              ry="15"
              fill="#1E293B"
              stroke="#1F1915"
              strokeWidth="2.2"
            />
            <path
              d="M22 36 Q34 31 46 37 L45 47 Q33 50 22 44 Z"
              fill="#FFFDF9"
            />
            {/* 修长锋利的金色鸟喙（直指树干） */}
            <polygon
              points="46,35 66,39 46,45"
              fill="#F59E0B"
              stroke="#1F1915"
              strokeWidth="2"
              strokeLinejoin="round"
            />
            {/* 啄击火花 */}
            {isWorking && (
              <g stroke="#EA580C" strokeWidth="2" strokeLinecap="round">
                <line x1="61" y1="31" x2="65" y2="27" />
                <line x1="61" y1="47" x2="65" y2="51" />
              </g>
            )}
            {/* 灵动鸟眼 */}
            <circle cx="36" cy="38" r="5" fill="#FBBF24" stroke="#1F1915" strokeWidth="1.8" />
            {isError ? (
              <path d="M34 36 L38 40 M38 36 L34 40" stroke="#DC2626" strokeWidth="2" strokeLinecap="round" />
            ) : (
              <>
                <circle cx="37" cy="38" r="2.5" fill="#1F1915" />
                <circle cx="38" cy="37" r="1" fill="#FFFFFF" />
              </>
            )}
          </g>
        </g>
      );

    case 'doctor':
      // 咖啡因大夫：外科反光额镜 + 白大褂与听诊器 + 冒热气的浓缩咖啡杯
      return (
        <g>
          {/* 白大褂与手术服内衬 */}
          <path d="M16 76 C16 57, 64 57, 64 76 Z" fill="#FFFFFF" stroke="#1F1915" strokeWidth="2" />
          <polygon points="32,59 40,73 48,59" fill="#0D9488" />
          {/* 挂在脖子上的听诊器 */}
          <path
            d="M26 60 C24 70, 32 74, 32 76 M54 60 C56 68, 48 72, 48 72"
            stroke="#334155"
            strokeWidth="2.5"
            fill="none"
            strokeLinecap="round"
          />
          <circle cx="48" cy="71" r="3.5" fill="#E2E8F0" stroke="#1F1915" strokeWidth="1.6" />

          {/* 头部与利落短发 */}
          <path d="M20 32 C20 14, 60 14, 60 32 Z" fill="#1E293B" />
          <rect
            x="22"
            y="22"
            width="36"
            height="35"
            rx="15"
            fill="#FFFDF9"
            stroke="#1F1915"
            strokeWidth="2.2"
          />

          {/* 外科医生头带与反光圆额镜 */}
          <path d="M21 26 Q40 30 59 26" stroke="#1F1915" strokeWidth="3.5" fill="none" />
          <circle cx="40" cy="24" r="8" fill="#E2E8F0" stroke="#1F1915" strokeWidth="2" />
          <circle cx="40" cy="24" r="4" fill="#FFFFFF" />
          <path d="M40 21 V27 M37 24 H43" stroke="#EF4444" strokeWidth="2" strokeLinecap="round" />

          {/* 专注眼神（带黑眼圈反差萌） */}
          <ellipse cx="32" cy="41" rx="3.5" ry="1.2" fill="#94A3B8" opacity="0.55" />
          <ellipse cx="48" cy="41" rx="3.5" ry="1.2" fill="#94A3B8" opacity="0.55" />
          {isError ? (
            <g stroke="#DC2626" strokeWidth="2.2" strokeLinecap="round">
              <path d="M29 35 L35 41 M35 35 L29 41" />
              <path d="M45 35 L51 41 M51 35 L45 41" />
            </g>
          ) : isCelebrate ? (
            <g stroke="#1F1915" strokeWidth="2.2" strokeLinecap="round" fill="none">
              <path d="M28 38 Q32 33 36 38" />
              <path d="M44 38 Q48 33 52 38" />
            </g>
          ) : (
            <g>
              <circle cx="32" cy="38" r="2.8" fill="#1F1915" />
              <circle cx="48" cy="38" r="2.8" fill="#1F1915" />
              <circle cx="33" cy="37" r="1" fill="#FFFFFF" />
              <circle cx="49" cy="37" r="1" fill="#FFFFFF" />
            </g>
          )}
          {/* 外科口罩 */}
          <rect x="28" y="44" width="24" height="11" rx="4" fill="#CCFBF1" stroke="#0D9488" strokeWidth="1.6" />
          <line x1="23" y1="45" x2="28" y2="47" stroke="#0D9488" strokeWidth="1.5" />
          <line x1="57" y1="45" x2="52" y2="47" stroke="#0D9488" strokeWidth="1.5" />

          {/* 左下角浓缩咖啡杯与上升热气 */}
          <g transform="translate(6, 48)">
            <path
              className="avatar-steam"
              d="M7 2 Q5 -2 8 -5 M13 2 Q11 -2 14 -5"
              stroke="#EA580C"
              strokeWidth="1.6"
              fill="none"
              strokeLinecap="round"
            />
            <rect x="3" y="4" width="14" height="14" rx="3" fill="#78350F" stroke="#1F1915" strokeWidth="1.8" />
            <path d="M17 7 H19 A2.5 2.5 0 0 1 19 13 H17" fill="none" stroke="#1F1915" strokeWidth="1.8" />
          </g>
        </g>
      );

    case 'patcher':
      // 爱较真的补丁匠：斜戴拼布贝雷帽 + 黄铜单眼放大镜(Monocle) + 针线与十字补丁
      return (
        <g>
          {/* 工匠背带裤与十字补丁 */}
          <path d="M17 76 C17 58, 63 58, 63 76 Z" fill="#9A3412" stroke="#1F1915" strokeWidth="2" />
          <rect x="27" y="62" width="11" height="10" rx="2" fill="#FDE68A" stroke="#1F1915" strokeWidth="1.6" />
          <line x1="25" y1="65" x2="29" y2="65" stroke="#1F1915" strokeWidth="1.5" />
          <line x1="36" y1="65" x2="40" y2="65" stroke="#1F1915" strokeWidth="1.5" />

          {/* 脸部 */}
          <rect
            x="22"
            y="24"
            width="36"
            height="34"
            rx="15"
            fill="#FFFDF9"
            stroke="#1F1915"
            strokeWidth="2.2"
          />

          {/* 斜戴的复古软呢贝雷帽 + 拼布缝线 */}
          <path
            d="M15 27 C15 11, 58 9, 64 24 C50 22, 26 24, 15 27 Z"
            fill="#EA580C"
            stroke="#1F1915"
            strokeWidth="2"
            strokeLinejoin="round"
          />
          <line x1="38" y1="12" x2="36" y2="9" stroke="#1F1915" strokeWidth="2.2" strokeLinecap="round" />
          <rect x="45" y="14" width="9" height="6" rx="1.5" fill="#FDE68A" stroke="#1F1915" strokeWidth="1.4" />

          {/* 左眼：黄铜精密单眼放大镜 (Monocle) */}
          <circle
            cx="32"
            cy="39"
            r="7.5"
            fill="rgba(56, 189, 248, 0.22)"
            stroke="#D97706"
            strokeWidth="2.6"
          />
          <path d="M25 41 C22 48, 22 55, 25 60" stroke="#D97706" strokeWidth="1.5" fill="none" strokeDasharray="2 2" />
          {/* 眼睛与翘胡子 */}
          {isError ? (
            <g stroke="#DC2626" strokeWidth="2.2" strokeLinecap="round">
              <path d="M29 36 L35 42 M35 36 L29 42" />
              <path d="M45 36 L51 42 M51 36 L45 42" />
            </g>
          ) : (
            <g>
              <circle cx="32" cy="39" r="3.2" fill="#1F1915" />
              <circle cx="33.2" cy="37.8" r="1.2" fill="#FFFFFF" />
              <circle cx="48" cy="39" r="2.6" fill="#1F1915" />
              <circle cx="49" cy="38" r="1" fill="#FFFFFF" />
            </g>
          )}
          {/* 小巧精致的八字翘胡与微笑 */}
          <path
            d="M33 48 Q37 45 40 48 Q43 45 47 48"
            stroke="#78350F"
            strokeWidth="2.2"
            fill="none"
            strokeLinecap="round"
          />
          {/* 右侧悬浮发光缝合银针与金线 */}
          <line x1="62" y1="34" x2="72" y2="22" stroke="#64748B" strokeWidth="2.2" strokeLinecap="round" />
          <path d="M62 34 Q58 44 68 48" stroke="#F59E0B" strokeWidth="1.6" fill="none" />
        </g>
      );

    case 'bughunter':
      // 戴眼镜的捉虫侠：酷炫极客游侠立领披风 + 超大黑框双层眼镜（镜片带绿色雷达准星）
      return (
        <g>
          {/* 游侠高立领披风 */}
          <path
            d="M14 76 L20 52 L30 60 L50 60 L60 52 L66 76 Z"
            fill="#312E81"
            stroke="#1F1915"
            strokeWidth="2"
            strokeLinejoin="round"
          />
          <polygon points="35,60 40,72 45,60" fill="#EF4444" />

          {/* 脸部与刺猬短发 */}
          <rect
            x="21"
            y="23"
            width="38"
            height="35"
            rx="14"
            fill="#FFFDF9"
            stroke="#1F1915"
            strokeWidth="2.2"
          />
          <path
            d="M19 28 L23 14 L31 20 L39 11 L46 19 L56 13 L61 28 C50 22, 30 22, 19 28 Z"
            fill="#1E1B4B"
            stroke="#1F1915"
            strokeWidth="1.8"
            strokeLinejoin="round"
          />

          {/* 标志性超大黑框极客眼镜 */}
          <rect
            x="20"
            y="31"
            width="18"
            height="14"
            rx="3.5"
            fill="#0F172A"
            stroke="#1F1915"
            strokeWidth="2.6"
          />
          <rect
            x="42"
            y="31"
            width="18"
            height="14"
            rx="3.5"
            fill="#0F172A"
            stroke="#1F1915"
            strokeWidth="2.6"
          />
          <line x1="38" y1="37" x2="42" y2="37" stroke="#1F1915" strokeWidth="3" />

          {/* 镜片内部：绿色 AST 雷达准星 / 报错红叉 */}
          {isError ? (
            <g stroke="#F87171" strokeWidth="2.2" strokeLinecap="round">
              <path d="M26 35 L32 41 M32 35 L26 41" />
              <path d="M48 35 L54 41 M54 35 L48 41" />
            </g>
          ) : (
            <g className={isWorking ? 'avatar-radar' : ''}>
              <circle cx="29" cy="38" r="4" fill="none" stroke="#4ADE80" strokeWidth="1.5" />
              <circle cx="29" cy="38" r="1.4" fill="#4ADE80" />
              <line x1="46" y1="35" x2="55" y2="35" stroke="#4ADE80" strokeWidth="1.6" strokeLinecap="round" />
              <line x1="46" y1="39" x2="52" y2="39" stroke="#38BDF8" strokeWidth="1.6" strokeLinecap="round" />
              <line x1="22" y1="33" x2="26" y2="43" stroke="rgba(255,255,255,0.28)" strokeWidth="2" />
            </g>
          )}

          {/* 自信上扬的嘴角 */}
          <path d="M35 51 Q40 54 46 49" stroke="#1F1915" strokeWidth="2.2" fill="none" strokeLinecap="round" />
        </g>
      );

    case 'perfectionist':
      // 零警告强迫症：圣堂银白骑士头盔/圣盾造型 + 0 WARN 金色光环 + 四周洁净星芒
      return (
        <g>
          {/* 头顶金色零警告光环 */}
          <ellipse
            cx="40"
            cy="11"
            rx="16"
            ry="4"
            fill="none"
            stroke="#F59E0B"
            strokeWidth="2.4"
          />
          {/* 四周闪烁的洁净四角星芒 */}
          <g className="avatar-sparkle" fill="#0891B2">
            <path d="M12 18 L14 22 L18 24 L14 26 L12 30 L10 26 L6 24 L10 22 Z" />
            <path d="M68 16 L69.5 19.5 L73 21 L69.5 22.5 L68 26 L66.5 22.5 L63 21 L66.5 19.5 Z" fill="#F59E0B" />
          </g>

          {/* 骑士肩甲与圣盾徽章 */}
          <path
            d="M16 76 C16 58, 64 58, 64 76 Z"
            fill="#0E7490"
            stroke="#1F1915"
            strokeWidth="2"
          />
          <polygon points="40,60 33,66 40,75 47,66" fill="#FBBF24" stroke="#1F1915" strokeWidth="1.5" />

          {/* 对称银白骑士头盔主体 */}
          <path
            d="M21 36 C21 18, 59 18, 59 36 L57 54 L40 60 L23 54 Z"
            fill="#F8FAFC"
            stroke="#1F1915"
            strokeWidth="2.2"
            strokeLinejoin="round"
          />
          <line x1="40" y1="19" x2="40" y2="30" stroke="#0891B2" strokeWidth="2.5" />

          {/* 头盔护目T型面甲 */}
          <rect x="25" y="31" width="30" height="12" rx="4" fill="#0F172A" stroke="#1F1915" strokeWidth="1.8" />
          {isError ? (
            <g stroke="#F87171" strokeWidth="2.2" strokeLinecap="round">
              <path d="M30 34 L36 40 M36 34 L30 40" />
              <path d="M44 34 L50 40 M50 34 L44 40" />
            </g>
          ) : (
            <g stroke="#2DD4BF" strokeWidth="2.4" strokeLinecap="round" fill="none">
              <path d="M30 37 L33 40 L38 34" />
              <path d="M43 37 L46 40 L51 34" />
            </g>
          )}
          {/* 面甲呼吸格栅 */}
          <line x1="34" y1="48" x2="46" y2="48" stroke="#64748B" strokeWidth="2" strokeLinecap="round" />
          <line x1="36" y1="52" x2="44" y2="52" stroke="#64748B" strokeWidth="2" strokeLinecap="round" />
        </g>
      );

    case 'traveler':
      // 断点旅行者：额头飞行护目镜 + 飘动霓虹围巾 + 胸前脉动的红色 Debug 断点能量球
      return (
        <g>
          {/* 背景时空轨迹环 */}
          <circle
            cx="40"
            cy="40"
            r="33"
            fill="none"
            stroke="rgba(232, 121, 249, 0.35)"
            strokeWidth="1.5"
            strokeDasharray="5 4"
          />
          {/* 飘动的霓虹紫围巾与风衣 */}
          <path d="M18 76 C18 58, 62 58, 62 76 Z" fill="#3B0764" stroke="#1F1915" strokeWidth="2" />
          <path
            d="M23 53 Q40 60 57 53 L68 62 L58 67 L48 59 Z"
            fill="#EC4899"
            stroke="#1F1915"
            strokeWidth="1.8"
            strokeLinejoin="round"
          />

          {/* 脸部与银白乱发 */}
          <rect
            x="22"
            y="22"
            width="36"
            height="34"
            rx="15"
            fill="#FFFDF9"
            stroke="#1F1915"
            strokeWidth="2.2"
          />
          <path d="M20 26 C22 12, 58 12, 60 26 Z" fill="#E2E8F0" stroke="#1F1915" strokeWidth="1.8" />

          {/* 推在额头上的黄铜飞行员护目镜 */}
          <rect x="21" y="18" width="38" height="10" rx="4" fill="#78350F" stroke="#1F1915" strokeWidth="1.8" />
          <circle cx="31" cy="23" r="5.5" fill="#38BDF8" stroke="#F59E0B" strokeWidth="2.2" />
          <circle cx="49" cy="23" r="5.5" fill="#38BDF8" stroke="#F59E0B" strokeWidth="2.2" />

          {/* 眼睛与表情 */}
          {isError ? (
            <g stroke="#DC2626" strokeWidth="2.2" strokeLinecap="round">
              <path d="M29 36 L35 42 M35 36 L29 42" />
              <path d="M45 36 L51 42 M51 36 L45 42" />
            </g>
          ) : (
            <g>
              <circle cx="32" cy="39" r="2.8" fill="#581C87" />
              <circle cx="48" cy="39" r="2.8" fill="#581C87" />
              <circle cx="33" cy="38" r="1.1" fill="#FFFFFF" />
              <circle cx="49" cy="38" r="1.1" fill="#FFFFFF" />
              <path d="M36 47 Q40 50 44 47" stroke="#1F1915" strokeWidth="2" fill="none" strokeLinecap="round" />
            </g>
          )}

          {/* 悬浮脉动的红色 Debug 断点核心球 */}
          <g className="avatar-orb">
            <circle cx="62" cy="48" r="9" fill="rgba(239, 68, 68, 0.25)" />
            <circle cx="62" cy="48" r="5.5" fill="#EF4444" stroke="#FFFFFF" strokeWidth="1.6" />
          </g>
        </g>
      );

    case 'archaeologist':
    default:
      // 日志考古家：印第安纳宽檐探险猎鹿帽 + 卡其探险服 + 手持大号琥珀放大镜破译日志
      return (
        <g>
          {/* 卡其色探险家猎装与领巾 */}
          <path d="M16 76 C16 58, 64 58, 64 76 Z" fill="#B45309" stroke="#1F1915" strokeWidth="2" />
          <polygon points="33,58 40,69 47,58" fill="#FDE68A" stroke="#1F1915" strokeWidth="1.5" />

          {/* 脸部 */}
          <rect
            x="22"
            y="24"
            width="36"
            height="34"
            rx="15"
            fill="#FFFDF9"
            stroke="#1F1915"
            strokeWidth="2.2"
          />

          {/* 宽檐印第安纳探险软帽 (Fedora) */}
          <path
            d="M22 24 C24 10, 56 10, 58 24 Z"
            fill="#92400E"
            stroke="#1F1915"
            strokeWidth="2"
          />
          <rect x="23" y="20" width="34" height="4" fill="#451A03" />
          <ellipse
            cx="40"
            cy="25"
            rx="27"
            ry="5.5"
            fill="#B45309"
            stroke="#1F1915"
            strokeWidth="2"
          />

          {/* 五官 */}
          {isError ? (
            <g stroke="#DC2626" strokeWidth="2.2" strokeLinecap="round">
              <path d="M29 36 L35 42 M35 36 L29 42" />
              <path d="M45 36 L51 42 M51 36 L45 42" />
            </g>
          ) : (
            <g>
              <circle cx="31" cy="39" r="2.6" fill="#1F1915" />
              <circle cx="32" cy="38" r="1" fill="#FFFFFF" />
              <circle cx="48" cy="39" r="2.6" fill="#1F1915" />
              <path d="M35 48 Q40 51 44 48" stroke="#1F1915" strokeWidth="2" fill="none" strokeLinecap="round" />
            </g>
          )}

          {/* 右侧手持大号探险放大镜（镜内浮现日志符号） */}
          <g className={isWorking ? 'avatar-magnifier' : ''}>
            <line x1="61" y1="55" x2="70" y2="66" stroke="#78350F" strokeWidth="4" strokeLinecap="round" />
            <circle
              cx="54"
              cy="46"
              r="11"
              fill="rgba(254, 243, 199, 0.88)"
              stroke="#D97706"
              strokeWidth="2.8"
            />
            <path d="M49 44 L52 46 L49 48 M54 48 H58" stroke="#92400E" strokeWidth="1.8" fill="none" strokeLinecap="round" strokeLinejoin="round" />
          </g>
        </g>
      );
  }
};

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
  const isError = status === 'error' || status === 'failed';
  const isCelebrate = status === 'awaiting_confirmation' || status === 'completed';
  const isWorking = status === 'busy';

  const dimensions = size === 'sm' ? 42 : size === 'lg' ? 68 : 54;

  return (
    <div className={`agent-avatar-wrap size-${size} ${mood.moodClass}`}>
      <div
        className={`agent-avatar-frame ${isWorking ? 'is-working' : ''} ${isCelebrate ? 'is-celebrate' : ''} ${isError ? 'is-error' : ''}`}
        style={{
          width: dimensions,
          height: dimensions,
          background: persona.bgGradient,
          borderColor: persona.accentColor,
        }}
        title={`${name} · ${persona.roleTitle}（${persona.specialty}）`}
      >
        <svg
          viewBox="0 0 80 80"
          width="100%"
          height="100%"
          aria-hidden="true"
          className="agent-avatar-svg"
        >
          <CharacterIllustration
            personaKey={persona.key}
            isError={isError}
            isCelebrate={isCelebrate}
            isWorking={isWorking}
          />
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
