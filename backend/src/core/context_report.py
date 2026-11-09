"""python -m src.core.context_report [--root data/agent_context] [--agent ID] [--json]."""
import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

from ..config import get_settings


def summarize(root: Path, agent_id=None):
    groups = defaultdict(lambda: dict(runs=0, finished_runs=0, submitted_runs=0, failed_runs=0, cancelled_runs=0,
        requests=0, requests_with_usage=0, input_tokens=0, output_tokens=0, cached_tokens=0,
        compactions=Counter(), compaction_ms=0, tokens_removed=0, target_missed=0, budget_blocked=0,
        evidence_saved=0, evidence_reads=0, evidence_truncated=0, uncertain_tools=0,
        repeated_tool_calls=0,
        unmatched_tool_starts=0, unmatched_compaction_starts=0, fallback_window_runs=0,
        estimate_ratios=[]))
    malformed = 0
    for path in sorted(root.glob('*/runs/*.jsonl')):
        records = []
        with path.open(encoding='utf-8') as file:
            for line in file:
                try:
                    row = json.loads(line)
                    if isinstance(row, dict) and row.get('schema_version') == 1:
                        records.append(row)
                    else:
                        malformed += 1
                except (ValueError, TypeError):
                    malformed += 1
        if not records or (agent_id and records[0].get('agent_id') != agent_id):
            continue
        first = records[0]
        key = (first.get('engine'), first.get('provider_id'), first.get('model'), first.get('phase'), first.get('policy_version'))
        stats = groups[key]
        starts, finishes = set(), set()
        signatures = set()
        estimates = {}
        compact_starts = compact_ends = 0
        for row in records:
            event = row.get('event')
            if event == 'run_start':
                stats['runs'] += 1
                stats['fallback_window_runs'] += row.get('window_source') == 'fallback_estimate'
            elif event == 'run_end':
                stats['finished_runs'] += 1
                field = {'submitted': 'submitted_runs', 'failed': 'failed_runs', 'cancelled': 'cancelled_runs'}.get(row.get('status'))
                if field:
                    stats[field] += 1
            elif event == 'request_start':
                stats['requests'] += 1
                estimates[row.get('request_id', row.get('turn'))] = row.get('estimated_input_tokens')
            elif event == 'request_end' and row.get('usage'):
                usage = row['usage']
                measured = usage.get('input_tokens')
                if isinstance(measured, (int, float)) and measured > 0:
                    stats['requests_with_usage'] += 1
                    for field in ('input_tokens', 'output_tokens', 'cached_tokens'):
                        stats[field] += usage.get(field) or 0
                    estimate = estimates.get(row.get('request_id', row.get('turn')))
                    if estimate:
                        stats['estimate_ratios'].append(round(measured / estimate, 3))
            elif event == 'compaction_start':
                compact_starts += 1
            elif event == 'compaction':
                compact_ends += 1
                stats['compactions'][row.get('status', 'unknown')] += 1
                stats['compaction_ms'] += row.get('duration_ms') or 0
                if row.get('status') == 'success':
                    stats['tokens_removed'] += max(0, (row.get('before_tokens') or 0) - (row.get('after_tokens') or 0))
                    stats['target_missed'] += row.get('target_met') is False
            elif event == 'budget_blocked':
                stats['budget_blocked'] += 1
            elif event == 'evidence_saved':
                stats['evidence_saved'] += 1
                stats['evidence_truncated'] += bool(row.get('truncated'))
            elif event == 'evidence_read':
                stats['evidence_reads'] += 1
            elif event == 'tool_start':
                starts.add(row.get('execution_id'))
                if row.get('args_sha256'):
                    signature = (row.get('tool'), row['args_sha256'])
                    stats['repeated_tool_calls'] += signature in signatures
                    signatures.add(signature)
            elif event == 'tool_end':
                finishes.add(row.get('execution_id'))
                stats['uncertain_tools'] += row.get('status') == 'uncertain'
        stats['unmatched_tool_starts'] += len(starts - finishes)
        stats['unmatched_compaction_starts'] += max(0, compact_starts - compact_ends)
    result = []
    for (engine, provider, model, phase, policy), stats in groups.items():
        ratios = sorted(stats.pop('estimate_ratios'))
        stats['input_actual_to_estimate_p95'] = ratios[min(len(ratios) - 1, int(len(ratios) * 0.95))] if ratios else None
        stats['runs_without_end'] = max(0, stats['runs'] - stats['finished_runs'])
        result.append(dict(engine=engine, provider_id=provider, model=model, phase=phase, policy_version=policy, **stats))
    return {'schema_version': 1, 'malformed_lines': malformed, 'groups': result}


def markdown(report):
    lines = ['# 上下文管理运行报告', '', '提交仅代表阶段工具已接受，不等同于最终 Bug 验收通过。未结束运行可能仍在执行，也可能异常中断。', '']
    if not report['groups']:
        lines.append('尚无符合条件的运行日志。新运行开始后会自动记录。')
    for group in report['groups']:
        lines += [f"## {group['engine']} / {group['provider_id']} / {group['model']} / {group['phase']} / 策略 v{group['policy_version']}", '',
            f"- 运行：{group['runs']}；阶段提交：{group['submitted_runs']}；失败：{group['failed_runs']}；取消：{group['cancelled_runs']}；未结束：{group['runs_without_end']}",
            f"- 请求：{group['requests']}；有实际输入用量：{group['requests_with_usage']}；实测输入/输出：{group['input_tokens']} / {group['output_tokens']} tokens；缓存输入：{group['cached_tokens']}",
            f"- 压缩结果：{json.dumps(group['compactions'], ensure_ascii=False)}；累计耗时：{group['compaction_ms']} ms；累计估算缩减：{group['tokens_removed']} tokens",
            f"- 未达压缩目标：{group['target_missed']}；预算阻塞：{group['budget_blocked']}；窗口使用兼容估算：{group['fallback_window_runs']} 次运行",
            f"- 证据保存/回查/截断：{group['evidence_saved']} / {group['evidence_reads']} / {group['evidence_truncated']}",
            f"- 同一运行内相同工具与参数再次调用：{group['repeated_tool_calls']}（可能是必要复测，需结合证据判断）",
            f"- 工具结果不确定：{group['uncertain_tools']}；工具缺少结束记录：{group['unmatched_tool_starts']}；压缩缺少结束记录：{group['unmatched_compaction_starts']}",
            f"- 实际输入 / 估算输入 P95：{group['input_actual_to_estimate_p95'] if group['input_actual_to_estimate_p95'] is not None else '暂无实测样本'}", '']
    lines += [f"无法解析或不支持的日志行：{report['malformed_lines']}", '',
        '优化顺序：先检查预算阻塞、失败与未结束记录，再分析实际/估算偏差、证据回查和压缩耗时；结合任务验收结果决定是否调整策略。不要仅追求压缩率。']
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path)
    parser.add_argument('--agent')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    root = args.root or Path(get_settings().database_url.replace('sqlite+aiosqlite:///', '')).parent / 'agent_context'
    report = summarize(root, args.agent)
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else markdown(report))


if __name__ == '__main__':
    main()
