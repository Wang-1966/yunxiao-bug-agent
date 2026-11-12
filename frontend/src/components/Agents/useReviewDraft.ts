import { useEffect, useRef, useState } from 'react';
import type { CriterionReview, RichInputValue } from '../../types';

interface ReviewDraft { feedback: RichInputValue; reviews: CriterionReview[] }
interface SavedDraft { version: string; value: ReviewDraft }
const empty = (): ReviewDraft => ({ feedback: { text: '', images: [] }, reviews: [] });
const memory = new Map<string, SavedDraft>();

// 图片与富文本一起存 IndexedDB，避免把 base64 图片放入容量很小的 localStorage。
async function storage(agentId: string, value?: SavedDraft | null): Promise<SavedDraft | undefined> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open('yunxiao-review-drafts', 1);
    request.onupgradeneeded = () => request.result.createObjectStore('drafts');
    request.onerror = () => reject(request.error);
    request.onsuccess = () => {
      const db = request.result;
      const transaction = db.transaction('drafts', value === undefined ? 'readonly' : 'readwrite');
      const store = transaction.objectStore('drafts');
      const operation = value === undefined ? store.get(agentId) : value === null ? store.delete(agentId) : store.put(value, agentId);
      transaction.oncomplete = () => { db.close(); resolve(value === undefined ? operation.result : undefined); };
      transaction.onerror = () => { db.close(); reject(transaction.error); };
      transaction.onabort = () => { db.close(); reject(transaction.error); };
    };
  });
}

export default function useReviewDraft(agentId: string, version?: string) {
  const [value, setValue] = useState<ReviewDraft>(empty);
  const [ready, setReady] = useState(false);
  const [stale, setStale] = useState(false);
  const [memoryOnly, setMemoryOnly] = useState(false);
  const latest = useRef(value);
  const loaded = useRef<string>();
  const cleared = useRef(false);
  const dirty = useRef(false);
  latest.current = value;

  const persist = (currentVersion: string) => {
    if (cleared.current || !dirty.current || loaded.current !== currentVersion) return;
    const record = { version: currentVersion, value: latest.current };
    memory.set(agentId, record);
    void storage(agentId, record).catch(() => setMemoryOnly(true));
  };

  useEffect(() => {
    if (!version) return;
    let active = true;
    setReady(false);
    cleared.current = false;
    dirty.current = false;
    const restore = async () => {
      let record = memory.get(agentId);
      if (!record) {
        try { record = await storage(agentId); }
        catch { if (active) setMemoryOnly(true); }
      }
      if (!active) return;
      const restored = record?.version === version ? record.value : empty();
      latest.current = restored;
      setValue(restored);
      setStale(!!record && record.version !== version);
      loaded.current = version;
      setReady(true);
    };
    void restore();
    return () => { active = false; persist(version); };
  }, [agentId, version]);

  useEffect(() => {
    if (!ready || !version) return;
    const timer = setTimeout(() => persist(version), 350);
    return () => clearTimeout(timer);
  }, [value, ready, version]);

  const change = (update: (prior: ReviewDraft) => ReviewDraft) => {
    dirty.current = true;
    cleared.current = false;
    setValue((prior) => { const next = update(prior); latest.current = next; return next; });
  };
  const clear = async () => {
    cleared.current = true;
    dirty.current = false;
    memory.delete(agentId);
    await storage(agentId, null).catch(() => undefined);
  };
  return { value, change, ready: ready && loaded.current === version, stale, memoryOnly, clear };
}
