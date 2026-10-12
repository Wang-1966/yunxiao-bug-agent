import { create } from 'zustand';
import type { AgentStatus } from '../types';

interface AgentStore {
  agents: AgentStatus[];
  loading: boolean;
  error: string | null;

  setAgents: (agents: AgentStatus[]) => void;
  updateAgent: (agentId: string, updates: Partial<AgentStatus>) => void;
  setLoading: (loading: boolean) => void;
  setError: (error: string | null) => void;
}

export const useAgentStore = create<AgentStore>((set) => ({
  agents: [],
  loading: false,
  error: null,

  setAgents: (agents) => set({ agents }),

  updateAgent: (agentId, updates) =>
    set((state) => ({
      agents: state.agents.map((agent) =>
        agent.agent_id === agentId ? { ...agent, ...updates } : agent
      ),
    })),

  setLoading: (loading) => set({ loading }),

  setError: (error) => set({ error }),
}));
