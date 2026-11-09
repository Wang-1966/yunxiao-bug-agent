import assert from 'node:assert/strict';
import { test } from 'node:test';
import { compactionMetric, estimateInput } from './context-metrics.mjs';

test('compaction outcome distinguishes failed, cancelled and successful SDK events', () => {
  assert.equal(compactionMetric({ reason: 'threshold', errorMessage: 'secret' }).status, 'failed');
  assert.equal(compactionMetric({ aborted: true }).status, 'cancelled');
  const metric = compactionMetric({ result: { summary: 'private summary', tokensBefore: 100, estimatedTokensAfter: 40 } });
  assert.equal(metric.status, 'success');
  assert.equal(metric.before_tokens, 100);
  assert.equal(metric.after_tokens, 40);
  assert.equal(JSON.stringify(metric).includes('private summary'), false);
});

test('fallback accounts for non-ASCII, tools, and images without counting base64 as text', () => {
  assert.ok(estimateInput('中文') > estimateInput('abcd'));
  assert.equal(estimateInput({ type: 'image', data: 'x'.repeat(100000) }), 4096);
  assert.ok(estimateInput({ tools: [{ arguments: 'x'.repeat(300) }] }) > 100);
});
