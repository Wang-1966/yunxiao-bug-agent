import type { RepairSummary as Summary } from '../../types';

export default function RepairSummary({ summary }: { summary?: Summary | null }) {
  return <dl className="repair-summary">
    <div><dt>根因</dt><dd>{summary?.root_cause || '根因尚未确认'}</dd></div>
    <div><dt>修复方案</dt><dd>{summary?.solution || '尚未形成修复结果'}</dd></div>
  </dl>;
}
