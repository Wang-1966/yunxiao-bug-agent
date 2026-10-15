import { useEffect, useState } from 'react';
import { Alert, Button, Skeleton, Space, message } from 'antd';
import { PlayCircleOutlined, ReloadOutlined } from '@ant-design/icons';
import axios from 'axios';
import type { Bug, BugSupplement, RichInputValue } from '../../types';
import { bugService } from '../../services/bugService';
import RichInput from '../Shared/RichInput';
import { editorContent, serializeEditor } from '../../utils/richInput';

function errorText(error: unknown, fallback: string) {
  const detail = axios.isAxiosError(error) ? error.response?.data?.detail : undefined;
  return typeof detail === 'string' ? detail : fallback;
}

function inputValue(saved: BugSupplement): RichInputValue {
  return serializeEditor(editorContent({ text: saved.notes, images: saved.images, document: saved.document }));
}

interface Props {
  bug: Bug;
  onStarted: () => void;
  onDirtyChange?: (dirty: boolean) => void;
}

export default function BugSupplementEditor({ bug, onStarted, onDirtyChange }: Props) {
  const [saved, setSaved] = useState<BugSupplement | null>(null);
  const [draft, setDraft] = useState<RichInputValue>({ text: '', images: [] });
  const [error, setError] = useState('');
  const [reload, setReload] = useState(0);
  const [saving, setSaving] = useState(false);
  const [reading, setReading] = useState(false);
  const dirty = !!saved && JSON.stringify(draft) !== JSON.stringify(inputValue(saved));
  const canStart = ['pending', 'failed'].includes(bug.status)
    && !['已关闭', '已修复', '暂不修复'].includes(bug.source_status || '');

  useEffect(() => {
    let active = true;
    setError('');
    bugService.supplement(bug.bug_id).then((data) => {
      if (!active) return;
      setSaved(data);
      setDraft(inputValue(data));
    }).catch((err) => {
      if (active) setError(errorText(err, '加载补充信息失败，请重试'));
    });
    return () => { active = false; };
  }, [bug.bug_id, reload]);

  useEffect(() => {
    onDirtyChange?.(dirty);
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ''; };
    if (dirty) window.addEventListener('beforeunload', warn);
    return () => { window.removeEventListener('beforeunload', warn); };
  }, [dirty, onDirtyChange]);

  useEffect(() => () => onDirtyChange?.(false), [onDirtyChange]);

  const save = async (start: boolean) => {
    if (!saved || saving || reading) return;
    setSaving(true);
    let persisted = false;
    try {
      const result = await bugService.saveSupplement(bug.bug_id, { notes: draft.text, images: draft.images, document: draft.document, revision: saved.revision });
      persisted = true;
      setSaved(result);
      setDraft(inputValue(result));
      if (start) {
        await bugService.submitTask({ bug_id: bug.bug_id, priority: 5, supplement_revision: result.revision });
        message.success('补充信息已保存，任务已加入修复队列');
        onStarted();
      } else message.success('补充信息已保存');
    } catch (err) {
      message.error((persisted ? '补充信息已保存，但修复未启动：' : '') + errorText(err, persisted ? '请稍后重试' : '保存失败，请稍后重试'));
    } finally { setSaving(false); }
  };

  return <section className="bug-supplement">
    <h2>补充修复信息</h2>
    <p className="description-empty">补充信息仅用于本地修复，从云效同步后仍会保留。</p>
    {error ? <Alert type="error" showIcon message={error} action={<Button icon={<ReloadOutlined />} onClick={() => setReload((value) => value + 1)}>重试</Button>} /> : !saved ? <Skeleton active paragraph={{ rows: 3 }} /> : <>
      {saved.image_warning && <Alert type="warning" showIcon message={saved.image_warning} />}
      <label htmlFor="bug-supplement-notes">补充说明（选填）</label>
      <RichInput id="bug-supplement-notes" value={draft} onChange={setDraft} disabled={saving}
        onBusyChange={setReading}
        placeholder="描述复现步骤、实际表现、预期结果；截图直接粘贴在这里…" />
      {!canStart && <p className="description-empty">当前状态不能启动新修复。保存的新资料会在下一轮启动时使用。</p>}
      <Space wrap>
        <Button disabled={(!dirty && !saved.image_warning) || saving || reading} onClick={() => void save(false)}>保存补充信息</Button>
        <Button type="primary" icon={<PlayCircleOutlined />} disabled={!canStart || saving || reading} loading={saving} onClick={() => void save(true)}>保存并开始修复</Button>
        <span className="description-empty">{reading ? '正在读取图片…' : dirty ? '有未保存的修改' : '已保存'}</span>
      </Space>
    </>}
  </section>;
}
