import { create } from 'zustand';
import type { LLMConfig } from '../types';

interface LLMConfigStore {
  config: LLMConfig | null;
  loading: boolean;
  error: string | null;

  setConfig: (config: LLMConfig) => void;
  setLoading: (loading: boolean) => void;
  setError: (error: string | null) => void;
}

export const useLLMConfigStore = create<LLMConfigStore>((set) => ({
  config: null,
  loading: false,
  error: null,

  setConfig: (config) => set({ config }),
  setLoading: (loading) => set({ loading }),
  setError: (error) => set({ error }),
}));
