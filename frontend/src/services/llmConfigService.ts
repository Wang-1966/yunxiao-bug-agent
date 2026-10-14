import api from './api';
import type { LLMConfig, LLMProvider, YunxiaoConfig } from '../types';

export const llmConfigService = {
  // 获取 LLM 配置
  async get() {
    const response = await api.get('/api/llm-config');
    return response.data as LLMConfig;
  },

  // 更新 LLM 配置
  async update(config: Partial<LLMConfig>) {
    const response = await api.put('/api/llm-config', config);
    return response.data as LLMConfig;
  },

  // 测试 LLM 连接
  async testConnection(provider: LLMProvider) {
    const response = await api.post('/api/llm-config/test', provider);
    return response.data;
  },

  // 向提供商的 /models 接口查询可用模型
  async fetchModels(provider: { provider_id?: string; base_url: string; api_key?: string }) {
    const response = await api.post('/api/llm-config/models', provider);
    return response.data.models as string[];
  },

  // 预估成本
  async estimateCost(bugCount: number) {
    const response = await api.post('/api/llm-config/estimate', null, {
      params: { bug_count: bugCount },
    });
    return response.data;
  },

  async getYunxiao() {
    const response = await api.get('/api/settings/yunxiao');
    return response.data as YunxiaoConfig;
  },

  async updateYunxiao(config: { api_base: string; org_id: string; project_id: string; access_token?: string }) {
    const response = await api.put('/api/settings/yunxiao', config);
    return response.data as YunxiaoConfig;
  },
};
