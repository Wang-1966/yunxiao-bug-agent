/**
 * Agent 造型分配：所有 Agent 用同一个小机器人形象，靠围巾颜色 + 一件配饰区分。
 * 6 色 × 4 配饰 = 24 种造型，同时在场的 Agent 不撞款。
 */

export const LOOK_COLORS = [
  { name: '红', hex: '#E5484D' },
  { name: '黄', hex: '#F5A524' },
  { name: '绿', hex: '#30A46C' },
  { name: '蓝', hex: '#3E7BFA' },
  { name: '紫', hex: '#8E4EC6' },
  { name: '粉', hex: '#E93D82' },
] as const;

export const LOOK_ACCESSORIES = [
  { key: 'none', label: '围巾' },
  { key: 'glasses', label: '眼镜' },
  { key: 'headphones', label: '耳机' },
  { key: 'hardhat', label: '安全帽' },
] as const;

export type Accessory = (typeof LOOK_ACCESSORIES)[number]['key'];

export interface AgentLook {
  color: string;
  colorName: string;
  accessory: Accessory;
  accessoryLabel: string;
}

const COMBOS = LOOK_COLORS.length * LOOK_ACCESSORIES.length;
const registry = new Map<string, number>();
let registryKey = '';

function hashName(name: string): number {
  let hash = 0;
  for (let i = 0; i < name.length; i++) hash = (hash * 31 + name.charCodeAt(i)) >>> 0;
  return hash;
}

function lookFromCombo(combo: number): AgentLook {
  const color = LOOK_COLORS[combo % LOOK_COLORS.length];
  const accessory = LOOK_ACCESSORIES[Math.floor(combo / LOOK_COLORS.length) % LOOK_ACCESSORIES.length];
  return { color: color.hex, colorName: color.name, accessory: accessory.key, accessoryLabel: accessory.label };
}

/**
 * 根据当前所有 Agent 名称分配造型：每个名字先取哈希对应的造型，撞款时顺延到下一个空位。
 * 按名称排序后分配，所以结果只和名字集合有关，不同浏览器看到的一致。
 */
export function assignAgentLooks(names: string[]): void {
  const unique = [...new Set(names.map((name) => (name || '').trim()))].sort((a, b) =>
    a.localeCompare(b, 'zh-CN'),
  );
  const key = unique.join('\n');
  if (key === registryKey) return;
  registryKey = key;
  registry.clear();
  const used = new Set<number>();
  for (const name of unique) {
    let combo = hashName(name) % COMBOS;
    for (let step = 0; step < COMBOS && used.has(combo); step++) combo = (combo + 1) % COMBOS;
    used.add(combo);
    registry.set(name, combo);
  }
}

export function resolveAgentLook(name: string): AgentLook {
  const clean = (name || '').trim();
  return lookFromCombo(registry.get(clean) ?? hashName(clean) % COMBOS);
}
