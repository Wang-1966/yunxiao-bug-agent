/** Project defaults cover configured models; Pi's catalog covers other known models. */
import { readFileSync } from 'node:fs';

const defaults = JSON.parse(readFileSync(new URL('./model-context-defaults.json', import.meta.url), 'utf8'));

export function resolveModelContext(runtime, { providerId, model, contextWindow }) {
  if (contextWindow != null) {
    if (!Number.isSafeInteger(contextWindow) || contextWindow < 8192 || contextWindow > 10000000) {
      throw new Error(`模型「${model}」的上下文窗口必须为 8192–10000000 tokens`);
    }
    return { contextWindow, windowSource: 'configured' };
  }
  const exact = providerId ? runtime.getModel(providerId, model) : undefined;
  const preset = defaults.models[model];
  if (preset && (!exact || exact.provider === preset.catalogProvider)) {
    return { contextWindow: preset.contextWindow, windowSource: 'project_default' };
  }
  const candidates = exact ? [exact] : runtime.getModels().filter((entry) => entry.id === model);
  const windows = new Set(candidates.map((entry) => entry.contextWindow));
  if (windows.size === 1) {
    const window = candidates[0].contextWindow;
    if (Number.isSafeInteger(window) && window >= 8192) {
      return { contextWindow: window, windowSource: 'pi_catalog' };
    }
  }
  throw new Error(`内置模型配置与 Pi 目录暂未收录提供商「${providerId || '未命名'}」模型「${model}」的明确上下文窗口，需更新服务端模型配置；不会按固定窗口估算。`);
}
