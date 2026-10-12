import { useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { Alert, Button, Descriptions, Image, Skeleton, Tag } from 'antd';
import { ArrowLeftOutlined, DownloadOutlined, ReloadOutlined } from '@ant-design/icons';
import { Link, useParams } from 'react-router-dom';
import axios from 'axios';
import DOMPurify from 'dompurify';
import { bugService } from '../../services/bugService';
import type { Bug } from '../../types';
import { getStatusLabel } from '../../utils/statusLabels';

function descriptionHtml(bug: Bug) {
  const html = DOMPurify.sanitize(bug.description || '', { USE_PROFILES: { html: true }, FORBID_TAGS: ['style', 'form', 'input', 'button'], FORBID_ATTR: ['style'] });
  const document = new DOMParser().parseFromString(html, 'text/html');
  document.querySelectorAll('[src], [href], [poster]').forEach((element) => {
    ['src', 'href', 'poster'].forEach((attribute) => {
      const original = element.getAttribute(attribute);
      const url = original && bug.media_urls?.[original];
      if (url?.startsWith('https://')) element.setAttribute(attribute, url);
    });
  });
  document.querySelectorAll('video, audio').forEach((element) => element.setAttribute('controls', ''));
  return document.body.innerHTML;
}

export default function BugDetail({ bugId: embeddedBugId, embedded = false }: { bugId?: string; embedded?: boolean } = {}) {
  const { bugId: routeBugId } = useParams();
  const bugId = embeddedBugId || routeBugId;
  const [bug, setBug] = useState<Bug | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [reload, setReload] = useState(0);
  const [imageError, setImageError] = useState(false);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setBug(null);
    setError('');
    setImageError(false);
    if (!bugId) return;
    bugService.get(bugId).then((data: Bug) => {
      if (active) setBug(data);
    }).catch((err: unknown) => {
      if (active) setError(axios.isAxiosError(err) && err.response?.status === 404 ? '该 Bug 不存在或已移除' : '加载详情失败，请稍后重试');
    }).finally(() => {
      if (active) setLoading(false);
    });
    return () => { active = false; };
  }, [bugId, reload]);

  const content: ReactNode = <>
    {!embedded && <div className="page-heading">
      <div><div className="page-eyebrow">BUG DETAIL</div><h1>Bug 详情</h1></div>
      <Link to="/bugs"><Button icon={<ArrowLeftOutlined />}>返回 Bug 列表</Button></Link>
    </div>}
    {loading ? <Skeleton active paragraph={{ rows: 8 }} /> : error ?
      <Alert type="error" showIcon message={error} action={<Button icon={<ReloadOutlined />} onClick={() => setReload((value) => value + 1)}>重试</Button>} /> : bug && <>
        <section className="bug-summary">
          <h2>{bug.title}</h2>
          <Descriptions column={{ xs: 1, sm: 2, lg: 3 }} items={[
            { key: 'id', label: 'Bug ID', children: bug.bug_id },
            { key: 'project', label: '云效项目', children: bug.source_project || '-' },
            { key: 'sourceStatus', label: '云效状态', children: bug.source_status || '-' },
            { key: 'priority', label: '优先级', children: <Tag>{({ critical: '关键', important: '重要', normal: '普通' })[bug.priority] || bug.priority}</Tag> },
            { key: 'status', label: '修复状态', children: getStatusLabel(bug.status) },
            { key: 'source', label: '来源', children: bug.source === 'yunxiao' ? '云效' : bug.source },
            { key: 'repo', label: '仓库路径', children: bug.repo_path || '-' },
            { key: 'branch', label: '分支', children: bug.branch || '-' },
            { key: 'test', label: '验证命令', children: bug.test_command || '未配置' },
            { key: 'agent', label: '分配 Agent', children: bug.assigned_agent_id || '-' },
          ]} />
        </section>
        <section className="bug-description"><h2>Bug 描述</h2>
          {imageError && <Alert type="warning" showIcon message="部分图片或视频加载失败，请刷新详情以重新获取文件地址。" action={<Button icon={<ReloadOutlined />} onClick={() => setReload((value) => value + 1)}>刷新</Button>} />}
          {bug.media_warning && <Alert type="warning" showIcon message={bug.media_warning} />}
          {bug.detail_warning && <Alert type="warning" showIcon message={bug.detail_warning} />}
          {bug.description ? bug.description_format === 'MARKDOWN' || bug.source !== 'yunxiao' ? <div className="rich-description">{bug.description}</div> : <div className="rich-description" onErrorCapture={(event) => { if (event.target instanceof HTMLImageElement || event.target instanceof HTMLMediaElement) setImageError(true); }} dangerouslySetInnerHTML={{ __html: descriptionHtml(bug) }} /> : !bug.detail_warning && <p className="description-empty">暂无描述</p>}
        </section>
        {!!bug.attachments?.length && <section className="bug-attachments">
          <h2>附件</h2>
          {bug.attachments.map((attachment) => {
            const suffix = (attachment.suffix || attachment.fileName.split('.').pop() || '').toLowerCase();
            const url = attachment.url?.startsWith('https://') ? attachment.url : undefined;
            return <div className="bug-attachment" key={attachment.fileId || attachment.id}>
              <div className="section-heading"><h3>{attachment.fileName}</h3>{url && <Button type="link" icon={<DownloadOutlined />} href={url} target="_blank" rel="noopener noreferrer">下载</Button>}</div>
              {url ? ['mp4', 'webm', 'ogg', 'mov'].includes(suffix) ? <video controls preload="metadata" src={url} onError={() => setImageError(true)} aria-label={attachment.fileName} /> : ['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp'].includes(suffix) ? <Image src={url} alt={attachment.fileName} onError={() => setImageError(true)} /> : null : <p className="description-empty">附件地址暂不可用，请刷新详情。</p>}
            </div>;
          })}
        </section>}
      </>}
  </>;
  return embedded ? <div className="bug-drawer-content">{content}</div> : <div>{content}</div>;
}
