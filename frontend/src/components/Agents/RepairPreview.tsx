import { useEffect, useRef, useState } from 'react';
import axios from 'axios';
import { Alert, Button, Collapse, Space } from 'antd';
import { agentService, type PreviewStatus } from '../../services/agentService';

export default function RepairPreview({ agentId, disabled }: { agentId: string; disabled: boolean }) {
  const [preview, setPreview] = useState<PreviewStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const active = useRef(true);
  const revision = useRef(0);
  const operating = useRef(false);

  const apply = (result: PreviewStatus) => setPreview(result);

  useEffect(() => {
    active.current = true;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const load = async () => {
      const requestRevision = revision.current;
      try {
        if (!operating.current) {
          const result = await agentService.previewStatus(agentId);
          if (!cancelled && !operating.current && requestRevision === revision.current) apply(result);
        }
      } catch { /* 操作失败在按钮请求中展示，轮询失败等待下次重试。 */ }
      if (!cancelled) timer = setTimeout(load, 2000);
    };
    void load();
    return () => {
      cancelled = true;
      active.current = false;
      clearTimeout(timer);
    };
  }, [agentId]);

  const operate = async (start: boolean) => {
    if (operating.current || disabled) return;
    revision.current += 1;
    operating.current = true;
    setBusy(true);
    setError('');
    try {
      const result = await (start ? agentService.preview(agentId) : agentService.stopPreview(agentId));
      if (active.current) apply(result);
    } catch (err) {
      if (active.current) {
        const detail = axios.isAxiosError(err) ? err.response?.data?.detail : undefined;
        setError(typeof detail === 'string' ? detail : '操作预览服务失败，请重试');
      }
    } finally {
      revision.current += 1;
      operating.current = false;
      if (active.current) setBusy(false);
    }
  };

  const running = preview?.status === 'running';
  const starting = preview?.status === 'starting';
  const fixed = preview?.mode === 'fixed';
  return <div style={{ marginTop: 12 }}>
    <Space wrap>
      <Button loading={busy || starting} disabled={disabled || running} onClick={() => void operate(true)}>{starting ? '正在启动预览' : preview?.status === 'failed' ? '重试预览' : '启动预览'}</Button>
      {running && preview.url && <Button href={preview.url} target="_blank" rel="noopener noreferrer">{fixed ? '打开入口' : '打开预览'}</Button>}
      {(running || starting) && <Button disabled={disabled || busy} onClick={() => void operate(false)}>{fixed ? '返回原项目' : '停止预览'}</Button>}
      <span>{running && fixed ? `已切换至修复工作树（${preview.url}），请刷新原页面验证` : running ? preview.url : starting && fixed ? '修复服务启动中，就绪后切换固定入口' : starting ? '正在启动预览服务' : fixed ? '修复预览已停止，请在项目管理查看原项目状态并刷新原页面' : '配置固定入口后，可在原浏览器页面直接验证'}</span>
    </Space>
    {(error || preview?.error) && <Alert type="error" showIcon message={error || preview?.error} style={{ marginTop: 8 }} />}
    {!!preview?.output && <Collapse size="small" style={{ marginTop: 8 }} items={[{
      key: 'output', label: '预览启动日志', children: <pre className="repair-output">{preview.output}</pre>,
    }]} />}
  </div>;
}
