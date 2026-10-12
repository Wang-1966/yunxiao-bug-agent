import api from './api';

export const agentService = {
  // 获取所有 Agent 状态
  async list() {
    const response = await api.get('/api/agents');
    return response.data;
  },

  // 获取 Agent 详情
  async get(agentId: string) {
    const response = await api.get(`/api/agents/${agentId}`);
    return response.data;
  },

  async diff(agentId: string) {
    const response = await api.get(`/api/agents/${encodeURIComponent(agentId)}/diff`);
    return response.data as { diff: string; truncated: boolean };
  },

  async confirm(agentId: string, confirmed: boolean, feedback: string, gitCommitHash?: string) {
    const response = await api.post(`/api/agents/${encodeURIComponent(agentId)}/confirmation`, { confirmed, feedback, git_commit_hash: gitCommitHash });
    return response.data;
  },

  async proposal(agentId: string) {
    const response = await api.get(`/api/agents/${encodeURIComponent(agentId)}/proposal`);
    return response.data;
  },

  async confirmProposal(agentId: string, proposalIndex: number, feedback: string, decisionId?: string) {
    const response = await api.post(`/api/agents/${encodeURIComponent(agentId)}/proposal-confirmation`, { proposal_index: proposalIndex, feedback, decision_id: decisionId });
    return response.data;
  },

  async retry(agentId: string) {
    const response = await api.post(`/api/agents/${encodeURIComponent(agentId)}/retry`);
    return response.data as { agent_id: string; task_id: string; mode: 'resume' | 'restart'; start_node?: string; stage_label?: string; reason: string };
  },

  // 暂停 Agent
  async pause(agentId: string) {
    const response = await api.post(`/api/agents/${agentId}/pause`);
    return response.data;
  },

  // 恢复 Agent
  async resume(agentId: string) {
    const response = await api.post(`/api/agents/${agentId}/resume`);
    return response.data;
  },

  // 停止 Agent
  async stop(agentId: string) {
    const response = await api.post(`/api/agents/${agentId}/stop`);
    return response.data;
  },

  async delete(agentId: string) {
    const response = await api.delete(`/api/agents/${encodeURIComponent(agentId)}`);
    return response.data;
  },
};
