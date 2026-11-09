import { useEffect, useMemo, useRef, useState } from 'react';
import type { KeyboardEvent, MouseEvent, ReactNode } from 'react';
import { Alert, Button, Descriptions, Image, Skeleton, Tag } from 'antd';
import { ArrowLeftOutlined, DownloadOutlined, ReloadOutlined } from '@ant-design/icons';
import { Link, useParams } from 'react-router-dom';
import axios from 'axios';
import DOMPurify from 'dompurify';
import { bugService } from '../../services/bugService';
import type { Bug } from '../../types';
import { sourceStatusClass } from '../../utils/statusLabels';
import BugSupplementEditor from './BugSupplementEditor';

function prepareDescription(bug: Bug) {
  const html = DOMPurify.sanitize(bug.description || '', {
    USE_PROFILES: { html: true },
    FORBID_TAGS: ['style', 'form', 'input', 'button'],
    FORBID_ATTR: ['style'],
  });
  const document = new DOMParser().parseFromString(html, 'text/html');
  document.querySelectorAll('[src], [href], [poster]').forEach((element) => {
    ['src', 'href', 'poster'].forEach((attribute) => {
      const original = element.getAttribute(attribute);
      const url = original && bug.media_urls?.[original];
      if (url?.startsWith('https://')) element.setAttribute(attribute, url);
    });
  });
  document.querySelectorAll('video, audio').forEach((element) => element.setAttribute('controls', ''));
  const images: string[] = [];
  document.querySelectorAll('img').forEach((element) => {
    const src = element.getAttribute('src');
    if (!src) return;
    element.setAttribute('data-preview-index', String(images.length));
    element.setAttribute('tabindex', '0');
    element.setAttribute('role', 'button');
    if (!element.getAttribute('alt')) element.setAttribute('alt', `描述图片 ${images.length + 1}`);
    if (!element.getAttribute('title')) element.setAttribute('title', '点击预览');
    images.push(src);
  });
  return { html: document.body.innerHTML, images };
}

export default function BugDetail({
  bugId: embeddedBugId,
  embedded = false,
  onStarted,
  onDirtyChange,
}: {
  bugId?: string;
  embedded?: boolean;
  onStarted?: () => void;
  onDirtyChange?: (dirty: boolean) => void;
} = {}) {
  const { bugId: routeBugId } = useParams();
  const bugId = embeddedBugId || routeBugId;
  const [bug, setBug] = useState<Bug | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [reload, setReload] = useState(0);
  const [imageError, setImageError] = useState(false);
  const previewTriggers = useRef<HTMLDivElement>(null);
  const prepared = useMemo(
    () => (bug && bug.description_format !== 'MARKDOWN' && bug.source === 'yunxiao' ? prepareDescription(bug) : null),
    [bug],
  );

  const openDescriptionImage = (event: MouseEvent<HTMLElement> | KeyboardEvent<HTMLElement>) => {
    if ('key' in event && event.key !== 'Enter' && event.key !== ' ') return;
    const target = event.target;
    if (!(target instanceof HTMLImageElement)) return;
    const index = Number(target.getAttribute('data-preview-index'));
    if (!Number.isInteger(index) || index < 0) return;
    const trigger = previewTriggers.current?.querySelectorAll<HTMLElement>('.ant-image')[index];
    if (!trigger) return;
    event.preventDefault();
    trigger.click();
  };

  useEffect(() => {
    let active = true;
    setLoading(true);
    setBug(null);
    setError('');
    setImageError(false);
    if (!bugId) return;
    bugService
      .get(bugId)
      .then((data: Bug) => {
        if (active) setBug(data);
      })
      .catch((err: unknown) => {
        if (active)
          setError(
            axios.isAxiosError(err) && err.response?.status === 404
              ? '该 Bug 不存在或已移除'
              : '加载详情失败，请稍后重试',
          );
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [bugId, reload]);

  const content: ReactNode = (
    <>
      {!embedded && (
        <div className="page-heading">
          <div>
            <div className="page-eyebrow">🔍 BUG DOSSIER</div>
            <h1>Bug 详情档案</h1>
          </div>
          <Link to="/bugs">
            <Button icon={<ArrowLeftOutlined />}>返回 Bug 列表</Button>
          </Link>
        </div>
      )}
      {loading ? (
        <Skeleton active paragraph={{ rows: 8 }} />
      ) : error ? (
        <Alert
          type="error"
          showIcon
          message={error}
          action={
            <Button icon={<ReloadOutlined />} onClick={() => setReload((value) => value + 1)}>
              重试
            </Button>
          }
        />
      ) : (
        bug && (
          <>
            <section className="bug-summary">
              <h2>{bug.title}</h2>
              <Descriptions
                column={{ xs: 1, sm: 2, lg: 3 }}
                items={[
                  { key: 'id', label: 'Bug ID', children: bug.bug_id },
                  { key: 'project', label: '云效项目', children: bug.source_project || '-' },
                  {
                    key: 'sourceStatus',
                    label: '云效状态',
                    children: bug.source_status ? (
                      <span className={sourceStatusClass(bug.source_status)}>{bug.source_status}</span>
                    ) : (
                      '-'
                    ),
                  },
                  {
                    key: 'priority',
                    label: '优先级',
                    children: (
                      <Tag>
                        {({ critical: '关键', important: '重要', normal: '普通' })[bug.priority] ||
                          bug.priority}
                      </Tag>
                    ),
                  },
                ]}
              />
            </section>
            <section className="bug-description">
              <h2>Bug 描述</h2>
              {imageError && (
                <Alert
                  type="warning"
                  showIcon
                  message="部分图片或视频加载失败，请刷新详情以重新获取文件地址。"
                  action={
                    <Button
                      icon={<ReloadOutlined />}
                      onClick={() => setReload((value) => value + 1)}
                    >
                      刷新
                    </Button>
                  }
                />
              )}
              {bug.media_warning && <Alert type="warning" showIcon message={bug.media_warning} />}
              {bug.detail_warning && <Alert type="warning" showIcon message={bug.detail_warning} />}
              {bug.description ? (
                prepared ? (
                  <>
                    <div
                      className="rich-description"
                      onClick={openDescriptionImage}
                      onKeyDown={openDescriptionImage}
                      onErrorCapture={(event) => {
                        if (
                          event.target instanceof HTMLImageElement ||
                          event.target instanceof HTMLMediaElement
                        )
                          setImageError(true);
                      }}
                      dangerouslySetInnerHTML={{ __html: prepared.html }}
                    />
                    {prepared.images.length > 0 && (
                      <div ref={previewTriggers} hidden>
                        {/* 正文里的 img 点这里，交给 Ant Design 打开预览。 */}
                        <Image.PreviewGroup>
                          {prepared.images.map((src, index) => (
                            <Image key={index} src={src} alt={`描述图片 ${index + 1}`} />
                          ))}
                        </Image.PreviewGroup>
                      </div>
                    )}
                  </>
                ) : (
                  <div className="rich-description">{bug.description}</div>
                )
              ) : (
                !bug.detail_warning && <p className="description-empty">暂无描述</p>
              )}
            </section>
            <BugSupplementEditor
              key={bug.bug_id}
              bug={bug}
              onDirtyChange={onDirtyChange}
              onStarted={() => {
                setBug({ ...bug, status: 'analyzing' });
                onStarted?.();
              }}
            />
            {!!bug.attachments?.length && (
              <section className="bug-attachments">
                <h2>附件</h2>
                {bug.attachments.map((attachment) => {
                  const suffix = (
                    attachment.suffix ||
                    attachment.fileName.split('.').pop() ||
                    ''
                  ).toLowerCase();
                  const url = attachment.url?.startsWith('https://') ? attachment.url : undefined;
                  return (
                    <div className="bug-attachment" key={attachment.fileId || attachment.id}>
                      <div className="section-heading">
                        <h3>{attachment.fileName}</h3>
                        {url && (
                          <Button
                            type="link"
                            icon={<DownloadOutlined />}
                            href={url}
                            target="_blank"
                            rel="noopener noreferrer"
                          >
                            下载
                          </Button>
                        )}
                      </div>
                      {url ? (
                        ['mp4', 'webm', 'ogg', 'mov'].includes(suffix) ? (
                          <video
                            controls
                            preload="metadata"
                            src={url}
                            onError={() => setImageError(true)}
                            aria-label={attachment.fileName}
                          />
                        ) : ['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp'].includes(suffix) ? (
                          <Image
                            src={url}
                            alt={attachment.fileName}
                            onError={() => setImageError(true)}
                          />
                        ) : null
                      ) : (
                        <p className="description-empty">附件地址暂不可用，请刷新详情。</p>
                      )}
                    </div>
                  );
                })}
              </section>
            )}
          </>
        )
      )}
    </>
  );
  return embedded ? <div className="bug-drawer-content">{content}</div> : <div>{content}</div>;
}
