// Telemetry only: this conservative estimate never rejects requests or controls compaction.
export function estimateInput(value) {
  if (typeof value === 'string') {
    let ascii = 0;
    let other = 0;
    for (const char of value) char.codePointAt(0) < 128 ? ascii++ : other++;
    return Math.ceil(ascii / 3) + other * 2;
  }
  if (Array.isArray(value)) return value.reduce((sum, item) => sum + estimateInput(item) + 4, 0);
  if (value && typeof value === 'object') {
    if (value.type === 'image' || value.type === 'image_url') return 4096;
    return Object.entries(value).reduce((sum, [key, item]) => sum + estimateInput(key) + estimateInput(item) + 2, 0);
  }
  return value == null ? 0 : 4;
}

export function compactionMetric(event, started) {
  return { type: 'context_metric', event: 'compaction', reason: event.reason,
    status: event.aborted ? 'cancelled' : event.result ? 'success' : 'failed',
    before_tokens: event.result?.tokensBefore, after_tokens: event.result?.estimatedTokensAfter,
    summary_chars: event.result?.summary.length, duration_ms: started ? Date.now() - started : 0,
    will_retry: event.willRetry };
}
