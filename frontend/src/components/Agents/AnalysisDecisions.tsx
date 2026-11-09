import { Button, Space } from 'antd';
import type { AnalysisReport } from '../../types';

export default function AnalysisDecisions({ analysis, onReply, disabled }: {
  analysis?: AnalysisReport | null;
  onReply?: (reply: string) => void;
  disabled?: boolean;
}) {
  const classified = analysis?.decision_schema_version === 1;
  const questions = analysis?.decision_questions || [];
  const legacy = analysis?.open_questions || [];
  const notes = [...(analysis?.assumptions || []).map(text => `采用的假设：${text}`),
    ...(analysis?.verification_limits || []).map(text => `验证局限：${text}`)];
  return <>
    {classified ? questions.map((item, index) => <section key={index} className="analysis-decision">
      <strong>{item.question}</strong>
      <p>{item.reason}</p>
      <Space wrap>{item.options.map((option, choice) => onReply
        ? <Button key={choice} disabled={disabled} onClick={() => onReply(`${item.question}\n我的选择：${option}`)}>{option}{choice === item.recommended_option ? '（推荐）' : ''}</Button>
        : <span key={choice}>{option}{choice === item.recommended_option ? '（推荐）' : ''}</span>)}</Space>
      <details><summary>已调查的依据</summary><p>{item.investigated}</p></details>
    </section>) : !!legacy.length && <><strong>需要你确认</strong><ul>{legacy.map((question, index) => <li key={index}>{question}</li>)}</ul></>}
    {!!notes.length && <details className="analysis-notes"><summary>假设与验证说明（无需逐项确认）</summary><ul>{notes.map((note, index) => <li key={index}>{note}</li>)}</ul></details>}
  </>;
}
