import { Alert, Collapse, Input, Radio, Tag } from 'antd';
import type { AcceptanceEvidence, CriterionReview, VerificationCheck } from '../../types';

function CheckRecord({ check }: { check: VerificationCheck }) {
  return <div className="evidence-record">
    <div className="evidence-record-meta">
      <Tag color={check.passed === true ? 'success' : check.passed === false ? 'error' : 'default'}>
        {check.passed === true ? '命令通过' : check.passed === false ? '命令失败' : '结果未知'} · 退出码 {check.exit_code ?? '—'}
      </Tag>
      {check.completed_at && <time dateTime={check.completed_at}>{new Date(check.completed_at).toLocaleString()}</time>}
      {check.duration_ms !== undefined && <span>{(check.duration_ms / 1000).toFixed(1)} 秒</span>}
    </div>
    <code>{check.command}</code>
    <pre className="repair-output">{check.output || '命令没有产生输出。'}</pre>
  </div>;
}

const labels = {
  passed: ['success', '关联测试命令通过'], failed: ['error', '关联测试命令失败'],
  not_run: ['default', '尚无执行记录'], manual: ['warning', '需人工验证'], stale: ['warning', '证据版本不一致'],
} as const;

export default function AcceptanceEvidencePanel({ report, reviews, onChange, disabled = false }: {
  report?: AcceptanceEvidence;
  reviews: CriterionReview[];
  onChange?: (reviews: CriterionReview[]) => void;
  disabled?: boolean;
}) {
  if (!report) return <Alert showIcon type="info" message="此任务尚未提供逐项验收证据，可查看下方原始验证记录。" />;
  const passed = report.criteria.filter((item) => item.automatic_status === 'passed').length;
  const confirmed = reviews.filter((item) => item.status === 'passed').length;
  const update = (id: string, changes: Partial<CriterionReview>) => {
    const prior = reviews.find((item) => item.criterion_id === id);
    if (!prior && !changes.status) return;
    const next = { criterion_id: id, status: 'passed' as const, feedback: '', ...prior, ...changes };
    onChange?.([...reviews.filter((item) => item.criterion_id !== id), next]);
  };
  return <section className="acceptance-evidence" aria-label="逐项验收证据">
    <div className="evidence-summary">
      <div><span className="evidence-eyebrow">验收证据</span><h2>逐项核对实际行为</h2></div>
      <div className="evidence-counts"><span>关联测试通过 <strong>{passed}/{report.criteria.length}</strong></span><span>人工确认 <strong>{confirmed}/{report.criteria.length}</strong></span></div>
    </div>
    <p className="evidence-explanation">系统记录命令的真实执行结果；每项的测试覆盖说明由 Agent 提供。请结合实际行为作最终判断。</p>
    {report.git_commit_hash && <div className="evidence-version">本轮提交 <code title={report.git_commit_hash}>{report.git_commit_hash.slice(0, 12)}</code></div>}
    {report.legacy && <Alert type="info" showIcon message="历史任务未保存逐项覆盖关系，已有命令记录仅供参考，请人工核对各项要求。" />}
    {!report.legacy && report.git_commit_hash && !report.current && <Alert type="warning" showIcon message="证据与当前验收版本未绑定，自动结果不能作为当前版本的通过依据。" />}
    {!!report.verification_limits.length && <Alert type="warning" showIcon message="验证范围与限制" description={<ul>{report.verification_limits.map((text, index) => <li key={index}>{text}</li>)}</ul>} />}
    {!report.criteria.length && <Alert type="info" showIcon message="该历史任务没有结构化验收条目，请结合原始 Bug、预览与检查记录验收。" />}
    <div className="evidence-criteria">
      {report.criteria.map((criterion, index) => {
        const review = reviews.find((item) => item.criterion_id === criterion.id);
        const [color, label] = labels[criterion.automatic_status];
        const checks = report.checks.filter((item) => criterion.evidence_ids.includes(item.id));
        return <article key={criterion.id} className={`evidence-criterion${review?.status === 'failed' ? ' is-failed' : ''}`}>
          <div className="evidence-criterion-heading"><span className="evidence-number">{String(index + 1).padStart(2, '0')}</span><h3>{criterion.text}</h3><Tag color={color}>{label}</Tag></div>
          <p className="evidence-coverage"><strong>{criterion.method === 'manual' ? '人工操作与预期：' : 'Agent 覆盖说明：'}</strong>{criterion.detail}</p>
          {!!criterion.test_files.length && <p className="evidence-test-files">测试文件：{criterion.test_files.join('、')}</p>}
          {!!checks.length && <Collapse size="small" items={[{ key: 'records', label: '查看关联执行证据', children: <>
            {report.reproduction_before?.reproduced && <div className="evidence-before"><strong>修复前：复现命令失败（退出码 {report.reproduction_before.exit_before}）</strong><code>{report.reproduction_before.command}</code><pre className="repair-output">{report.reproduction_before.output_before || '未保存输出'}</pre></div>}
            {checks.map((check) => <CheckRecord key={check.id} check={check} />)}
          </> }]} />}
          <div className="evidence-human-review"><span>我的验收</span>{onChange ? <Radio.Group size="small" optionType="button" buttonStyle="solid" disabled={disabled} value={review?.status} onChange={(event) => update(criterion.id, { status: event.target.value })} options={[{ label: '通过', value: 'passed' }, { label: '未通过', value: 'failed' }]} /> : <Tag color={review?.status === 'passed' ? 'success' : review?.status === 'failed' ? 'error' : 'default'}>{review?.status === 'passed' ? '通过' : review?.status === 'failed' ? '未通过' : '未记录'}</Tag>}</div>
          {(review?.status === 'failed' || review?.feedback) && <Input.TextArea value={review.feedback} readOnly={!onChange} disabled={disabled} maxLength={1000} autoSize={{ minRows: 2, maxRows: 5 }} aria-label={`第 ${index + 1} 项验收反馈`} placeholder="描述仍存在的现象，帮助下一轮修复定位遗漏…" onChange={(event) => update(criterion.id, { feedback: event.target.value })} />}
        </article>;
      })}
    </div>
    {!!report.checks.length && <Collapse className="evidence-project-checks" items={[{ key: 'project', label: `系统检查记录（${report.checks.length}）`, children: report.checks.map((check) => <section key={check.id}><h3>{check.label}</h3><CheckRecord check={check} /></section>) }]} />}
  </section>;
}
