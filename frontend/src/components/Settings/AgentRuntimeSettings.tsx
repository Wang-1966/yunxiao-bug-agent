import { useEffect, useState } from 'react';
import { Alert, Card, Radio, message } from 'antd';
import axios from 'axios';
import api from '../../services/api';

export default function AgentRuntimeSettings() {
  const [runtime, setRuntime] = useState<{ engine: 'native' | 'pi'; pi_installed: boolean } | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    let cancelled = false;
    api.get('/api/settings/agent-runtime').then(({ data }) => { if (!cancelled) setRuntime(data); })
      .catch(() => { if (!cancelled) setError('无法读取执行内核配置，请刷新重试'); });
    return () => { cancelled = true; };
  }, []);
  const change = async (engine: 'native' | 'pi') => {
    setSaving(true);
    try {
      const { data } = await api.put('/api/settings/agent-runtime', { engine });
      setRuntime(data);
      message.success('执行内核已更新');
    } catch (err) { message.error(axios.isAxiosError(err) ? err.response?.data?.detail || '保存失败' : '保存失败'); }
    finally { setSaving(false); }
  };
  return <Card title="修复执行内核" style={{ marginBottom: 24 }}>
    {error ? <Alert type="error" message={error} /> : <Radio.Group value={runtime?.engine} disabled={!runtime || saving} onChange={(event) => void change(event.target.value)}>
      <Radio.Button value="native">现有执行器</Radio.Button>
      <Radio.Button value="pi" disabled={!runtime?.pi_installed}>Pi</Radio.Button>
    </Radio.Group>}
    <p style={{ color: 'var(--muted)', marginBottom: 0 }}>Pi 管理模型与会话循环，沿用当前模型配置、隔离工具、验证与验收规则。运行期间不能切换。</p>
    {runtime && !runtime.pi_installed && <Alert style={{ marginTop: 12 }} type="info" message="Pi 尚未安装" description="在 backend/pi 目录执行 npm ci，需要 Node.js 22.19 或更新版本。" />}
  </Card>;
}
