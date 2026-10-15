import type { Bug } from '../types';

export const repairStatusLabels: Record<Bug['status'], string> = {
  pending: '待处理',
  analyzing: '分析中',
  fixing: '修复中',
  testing: '测试中',
  awaiting_plan_confirmation: '待确认方案',
  awaiting_confirmation: '待你确认',
  resolved: '已解决',
  failed: '失败',
};

const statusLabels: Record<string, string> = {
  ...repairStatusLabels,
  idle: '空闲',
  busy: '执行中',
  paused: '已暂停',
  error: repairStatusLabels.failed,
  queued: '排队中',
  proposing: '方案生成中',
  verifying: repairStatusLabels.testing,
  completed: repairStatusLabels.resolved,
  rejected: '未修复',
  cancelled: '已停止',
};

export function getStatusLabel(status?: string): string {
  if (!status) return '-';
  return Object.hasOwn(statusLabels, status) ? statusLabels[status] : '未知状态';
}

const sourceStatusTones: Record<string, string> = {
  待确认: 'todo',
  处理中: 'doing',
  暂不修复: 'hold',
  已修复: 'done',
  已关闭: 'closed',
};

export function sourceStatusClass(status: string): string {
  return `yunxiao-status yunxiao-status-${sourceStatusTones[status] || 'other'}`;
}
