import assert from 'node:assert/strict';
import { test } from 'node:test';
import { ModelRuntime } from '@earendil-works/pi-coding-agent';
import { readFileSync } from 'node:fs';
import { resolveModelContext } from './model-context.mjs';

const models = [
  { provider: 'a', id: 'shared', contextWindow: 64000 },
  { provider: 'b', id: 'shared', contextWindow: 1000000 },
  { provider: 'a', id: 'other', contextWindow: 200000 },
  { provider: 'b', id: 'other', contextWindow: 200000 },
];
const catalog = {
  getModels: () => models,
  getModel: (provider, id) => models.find((entry) => entry.provider === provider && entry.id === id),
};

test('provider/model override wins; switching model and provider resolves different windows', () => {
  assert.deepEqual(resolveModelContext(catalog, { providerId: 'a', model: 'shared', contextWindow: 32000 }),
    { contextWindow: 32000, windowSource: 'configured' });
  assert.equal(resolveModelContext(catalog, { providerId: 'a', model: 'shared' }).contextWindow, 64000);
  assert.equal(resolveModelContext(catalog, { providerId: 'b', model: 'shared' }).contextWindow, 1000000);
  assert.equal(resolveModelContext(catalog, { providerId: 'a', model: 'other' }).contextWindow, 200000);
});

test('unknown relay uses only an unambiguous exact model match', () => {
  assert.equal(resolveModelContext(catalog, { providerId: 'relay', model: 'other' }).contextWindow, 200000);
  for (const model of ['shared', 'unknown', 'other-alias']) {
    assert.throws(() => resolveModelContext(catalog, { providerId: 'relay', model }), /需更新服务端模型配置/);
  }
  assert.throws(() => resolveModelContext(catalog, { model: 'shared', contextWindow: 0 }), /必须为/);
});

test('project defaults cover relay models without user input and retain source provenance', async () => {
  const defaults = JSON.parse(readFileSync(new URL('./model-context-defaults.json', import.meta.url), 'utf8'));
  const runtime = await ModelRuntime.create({ modelsPath: null });
  for (const [model, entry] of Object.entries(defaults.models)) {
    assert.equal(entry.contextWindow, runtime.getModel(entry.catalogProvider, model).contextWindow);
    assert.deepEqual(resolveModelContext(runtime, { providerId: 'custom-relay', model }),
      { contextWindow: entry.contextWindow, windowSource: 'project_default' });
  }
  assert.equal(resolveModelContext(runtime, { providerId: 'openai', model: 'gpt-6.1-sol' }).windowSource, 'project_default');
  assert.deepEqual(resolveModelContext(runtime, { providerId: 'openai', model: 'gpt-6.1-sol', contextWindow: 1000000 }),
    { contextWindow: 1000000, windowSource: 'configured' });
  const azure = runtime.getModel('azure', 'gpt-5.5');
  assert.equal(resolveModelContext(runtime, { providerId: 'azure', model: azure.id }).contextWindow, azure.contextWindow);
});

test('known model capabilities come from the installed Pi catalog', async () => {
  const runtime = await ModelRuntime.create({ modelsPath: null });
  const model = runtime.getModel('openai', 'gpt-4.1');
  assert.ok(model);
  assert.deepEqual(resolveModelContext(runtime, { providerId: 'openai', model: model.id }),
    { contextWindow: model.contextWindow, windowSource: 'pi_catalog' });
});
