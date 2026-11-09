import { useEffect, useState } from 'react';
import { Button, Drawer, Image, Modal } from 'antd';
import DOMPurify from 'dompurify';
import { bugService } from '../../services/bugService';
import api from '../../services/api';
import type { Bug, BugSupplement } from '../../types';
import BugDetail from '../Bugs/BugDetail';

export default function BugInputMessage({ bug }: { bug: Bug }) {
  const [detail, setDetail] = useState<Bug>(bug);
  const [supplement, setSupplement] = useState<BugSupplement | null>(null);
  const [failed, setFailed] = useState(false);
  const [recordOpen, setRecordOpen] = useState(false);
  const [recordDirty, setRecordDirty] = useState(false);
  const closeRecord = () => {
    const close = () => { setRecordOpen(false); setRecordDirty(false); };
    if (recordDirty) {
      Modal.confirm({
        title: '补充信息尚未保存',
        content: '关闭原始记录将丢弃修改。',
        okText: '丢弃并关闭',
        cancelText: '继续编辑',
        onOk: close,
      });
    } else close();
  };
  useEffect(() => {
    let cancelled = false;
    void Promise.allSettled([bugService.get(bug.bug_id), bugService.supplement(bug.bug_id)]).then(([details, extra]) => {
      if (cancelled) return;
      if (details.status === 'fulfilled') setDetail(details.value);
      if (extra.status === 'fulfilled') setSupplement(extra.value);
      setFailed(details.status === 'rejected' || extra.status === 'rejected');
    });
    return () => { cancelled = true; };
  }, [bug.bug_id]);
  const format = detail.description_format?.toUpperCase();
  const isHtml = format === 'HTML' || format === 'RICHTEXT' || (!format && detail.source === 'yunxiao');
  const html = DOMPurify.sanitize(detail.description || '', { USE_PROFILES: { html: true }, FORBID_TAGS: ['style', 'form', 'input', 'button'], FORBID_ATTR: ['style'] });
  const document = new DOMParser().parseFromString(html, 'text/html');
  document.querySelectorAll('[src], [href]').forEach((element) => {
    ['src', 'href'].forEach((attribute) => {
      const original = element.getAttribute(attribute);
      const url = original && detail.media_urls?.[original];
      if (url?.startsWith('https://')) element.setAttribute(attribute, url);
    });
  });
  return <div className="conversation-message is-user conversation-bug-input">
    <div className="conversation-text"><strong>{bug.title}</strong></div>
    {detail.description && (isHtml
      ? <div className="conversation-bug-description" dangerouslySetInnerHTML={{ __html: document.body.innerHTML }} />
      : <div className="conversation-text">{detail.description}</div>)}
    {supplement?.notes && <div className="conversation-text">{supplement.notes}</div>}
    {!!supplement?.images?.length && <Image.PreviewGroup>{supplement.images.map((src, index) => <Image key={src} src={src.startsWith('/') ? `${api.defaults.baseURL}${src}` : src} width={160} alt={`问题截图 ${index + 1}`} />)}</Image.PreviewGroup>}
    {detail.attachments?.map((attachment) => attachment.url?.startsWith('https://') && <div key={attachment.id}>
      {/\.(png|jpe?g|gif|webp)$/i.test(attachment.fileName) ? <Image src={attachment.url} width={160} alt={attachment.fileName} /> : <a href={attachment.url} target="_blank" rel="noopener noreferrer">{attachment.fileName}</a>}
    </div>)}
    {failed && <div className="transcript-note is-warning">部分问题资料加载失败，可打开原始记录查看。</div>}
    <Button type="link" className="conversation-record-link" onClick={() => setRecordOpen(true)}>查看原始记录</Button>
    <Drawer title="Bug 原始记录" placement="right" width="min(960px, 100vw)"
      rootClassName="bug-detail-drawer" open={recordOpen} onClose={closeRecord} destroyOnClose>
      <BugDetail bugId={bug.bug_id} embedded onDirtyChange={setRecordDirty} />
    </Drawer>
  </div>;
}
