import api from './api';
import type { AgentLiveStream, AgentToolActivity, AgentTranscriptEntry, RichInputNode, RichInputValue, ConversationMessage } from '../types';
import { assignAgentLooks } from '../utils/agentLooks';

export interface PreviewStatus {
  status: 'stopped' | 'starting' | 'running' | 'failed';
  url: string | null;
  port?: number;
  mode?: 'fixed' | 'direct';
  git_commit_hash?: string;
  output: string;
  error: string;
}

export const agentService = {
  // 获取所有 Agent 状态
  async list() {
    const response = await api.get('/api/agents');
    // 按全部 Agent 名称分配围巾颜色 + 配饰，保证同时在场的 Agent 不撞款
    assignAgentLooks((response.data || []).map((agent: { name: string }) => agent.name));
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

  async preview(agentId: string) {
    return (await api.post(`/api/agents/${encodeURIComponent(agentId)}/preview`)).data as PreviewStatus;
  },

  async previewStatus(agentId: string) {
    return (await api.get(`/api/agents/${encodeURIComponent(agentId)}/preview`)).data as PreviewStatus;
  },

  async stopPreview(agentId: string) {
    return (await api.delete(`/api/agents/${encodeURIComponent(agentId)}/preview`)).data as PreviewStatus;
  },

  async confirm(agentId: string, confirmed: boolean, feedback: string, gitCommitHash?: string, images: string[] = [], document?: RichInputNode) {
    const response = await api.post(`/api/agents/${encodeURIComponent(agentId)}/confirmation`, { confirmed, feedback, images, document, git_commit_hash: gitCommitHash });
    return response.data;
  },

  async retryYunxiaoStatusSync(agentId: string) {
    return (await api.post(`/api/agents/${encodeURIComponent(agentId)}/yunxiao-status-sync`)).data;
  },

  async proposal(agentId: string) {
    const response = await api.get(`/api/agents/${encodeURIComponent(agentId)}/proposal`);
    return response.data;
  },

  async confirmProposal(agentId: string, proposalIndex: number, feedback: string, decisionId?: string, images: string[] = [], document?: RichInputNode) {
    const response = await api.post(`/api/agents/${encodeURIComponent(agentId)}/proposal-confirmation`, { proposal_index: proposalIndex, feedback, images, document, decision_id: decisionId });
    return response.data;
  },

  async transcript(agentId: string, after = 0) {
    const response = await api.get(`/api/agents/${encodeURIComponent(agentId)}/transcript`, { params: { after } });
    return response.data as { entries: AgentTranscriptEntry[]; live: AgentLiveStream | null; activity?: AgentToolActivity | null };
  },

  async conversation(agentId: string) {
    return (await api.get(`/api/agents/${encodeURIComponent(agentId)}/conversation`)).data as {
      messages: ConversationMessage[]; discussing: boolean; engine: 'native' | 'pi'; status: string; can_send: boolean;
    };
  },

  async sendMessage(agentId: string, value: RichInputValue, clientId: string) {
    return (await api.post(`/api/agents/${encodeURIComponent(agentId)}/conversation`, {
      text: value.text, images: value.images, document: value.document, client_id: clientId,
    })).data as { id: number; status: string; retry?: { agent_id: string } };
  },

  async retry(agentId: string) {
    const response = await api.post(`/api/agents/${encodeURIComponent(agentId)}/retry`);
    return response.data as { agent_id: string; task_id: string; mode: 'resume' | 'restart'; start_node?: string; stage_label?: string; reason: string };
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
