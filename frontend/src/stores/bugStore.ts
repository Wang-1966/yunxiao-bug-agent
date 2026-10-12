import { create } from 'zustand';
import type { Bug } from '../types';

interface BugStore {
  bugs: Bug[];
  selectedBug: Bug | null;
  loading: boolean;
  error: string | null;

  setBugs: (bugs: Bug[]) => void;
  addBug: (bug: Bug) => void;
  updateBug: (bugId: string, updates: Partial<Bug>) => void;
  deleteBug: (bugId: string) => void;
  setSelectedBug: (bug: Bug | null) => void;
  setLoading: (loading: boolean) => void;
  setError: (error: string | null) => void;
}

export const useBugStore = create<BugStore>((set) => ({
  bugs: [],
  selectedBug: null,
  loading: false,
  error: null,

  setBugs: (bugs) => set({ bugs }),

  addBug: (bug) =>
    set((state) => ({
      bugs: [...state.bugs, bug],
    })),

  updateBug: (bugId, updates) =>
    set((state) => ({
      bugs: state.bugs.map((bug) =>
        bug.bug_id === bugId ? { ...bug, ...updates } : bug
      ),
      selectedBug:
        state.selectedBug?.bug_id === bugId
          ? { ...state.selectedBug, ...updates }
          : state.selectedBug,
    })),

  deleteBug: (bugId) =>
    set((state) => ({
      bugs: state.bugs.filter((bug) => bug.bug_id !== bugId),
      selectedBug:
        state.selectedBug?.bug_id === bugId ? null : state.selectedBug,
    })),

  setSelectedBug: (bug) => set({ selectedBug: bug }),

  setLoading: (loading) => set({ loading }),

  setError: (error) => set({ error }),
}));
