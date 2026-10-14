import { useEffect, useRef, useState } from 'react';
import axios from 'axios';
import { Alert, Button, Collapse, Popconfirm, Space, Tag } from 'antd';
import { projectService, type DevelopmentStatus } from '../../services/projectService';

export default function ProjectDevelopment({ projectId, port }: { projectId: string; port: number }) {
  const [status, setStatus] = useState<DevelopmentStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const revision = useRef(0);
  const operating = useRef(false);
  const active = useRef(true);

  useEffect(() => {
    active.current = true;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const load = async () => {
      const requestRevision = revision.current;
      try {
        if (!operating.current) {
          const result = await projectService.development(projectId);
          if (!cancelled && !operating.current && requestRevision === revision.current) setStatus(result);
        }
      } catch { /* 操作请求显示错误，轮询失败等待下次重试。 */ }
      if (!cancelled) timer = setTimeout(load, 2000);
    };
    void load();
    return () => { cancelled = true; active.current = false; clearTimeout(timer); };
  }, [projectId, port]);

  const operate = async (start: boolean) => {
    if (operating.current) return;
    revision.current += 1;
    operating.current = true;
    setBusy(true);
    setError('');
    try {
      const result = await (start ? projectService.startDevelopment(projectId) : projectService.stopDevelopment(projectId));
      if (active.current) setStatus(result);
    } catch (err) {
      if (active.current) {
        const detail = axios.isAxiosError(err) ? err.response?.data?.detail : undefined;
        setError(typeof detail === 'string' ? detail : '项目服务操作失败，请重试');
      }
    } finally {
      revision.current += 1;
      operating.current = false;
      if (active.current) setBusy(false);
    }
  };

  const starting = status?.status === 'starting';
  const running = status?.status === 'running';
  return <div>
    <Space wrap>
      <span>localhost:{port}</span>
      {running && <Tag color={status.source === 'repair' ? 'orange' : 'green'}>{status.source === 'repair' ? '展示修复结果' : '原项目'}</Tag>}
      <Button size="small" loading={busy || starting} disabled={running} onClick={() => void operate(true)}>{starting ? '正在启动' : '启动项目'}</Button>
      {running && status.url && <Button size="small" href={status.url} target="_blank" rel="noopener noreferrer">打开项目</Button>}
      {(running || starting || status?.status === 'failed') && <Popconfirm title="停止项目？" description="固定入口和该仓库的验收预览将一起停止。" onConfirm={() => operate(false)} okText="停止" cancelText="取消">
        <Button size="small" disabled={busy}>停止项目</Button>
      </Popconfirm>}
    </Space>
    {(error || status?.error) && <Alert type="error" showIcon message={error || status?.error} style={{ marginTop: 8 }} />}
    {!!status?.output && <Collapse size="small" style={{ marginTop: 8 }} items={[{
      key: 'output', label: '项目启动日志', children: <pre className="repair-output">{status.output}</pre>,
    }]} />}
  </div>;
}
