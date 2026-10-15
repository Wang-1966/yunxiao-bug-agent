import { create } from 'zustand';

export type UIThemeMode = 'warm' | 'arcade' | 'cyber';

export interface ThemeMeta {
  id: UIThemeMode;
  name: string;
  shortName: string;
  tagline: string;
  badge: string;
  primaryColor: string;
  isDark?: boolean;
}

export const UI_THEMES: Record<UIThemeMode, ThemeMeta> = {
  warm: {
    id: 'warm',
    name: '暖调工作台',
    shortName: '暖调',
    tagline: '陶土暖色 · 手作质感与灵动角色插画',
    badge: '☀️ 暖调',
    primaryColor: '#DA7756',
    isDark: false,
  },
  arcade: {
    id: 'arcade',
    name: '复古街机悬赏',
    shortName: '街机',
    tagline: '新粗野主义描边 · 闯关悬赏街机感',
    badge: '🕹️ 街机',
    primaryColor: '#E85D3F',
    isDark: false,
  },
  cyber: {
    id: 'cyber',
    name: '极客暗夜终端',
    shortName: '暗夜',
    tagline: '深空霓虹 HUD · 沉浸式代码实验室',
    badge: '⚡ 暗夜',
    primaryColor: '#38BDF8',
    isDark: true,
  },
};

const STORAGE_KEY = 'yunxiao_ui_theme_mode';

function getInitialTheme(): UIThemeMode {
  try {
    const saved = localStorage.getItem(STORAGE_KEY) as UIThemeMode | null;
    if (saved && saved in UI_THEMES) {
      return saved;
    }
  } catch {
    // ignore storage errors
  }
  return 'warm';
}

interface ThemeStore {
  themeMode: UIThemeMode;
  setThemeMode: (mode: UIThemeMode) => void;
  cycleTheme: () => void;
}

export const useThemeStore = create<ThemeStore>((set, get) => ({
  themeMode: getInitialTheme(),
  setThemeMode: (mode: UIThemeMode) => {
    try {
      localStorage.setItem(STORAGE_KEY, mode);
    } catch {
      // ignore storage errors
    }
    if (typeof document !== 'undefined') {
      document.documentElement.setAttribute('data-theme', mode);
    }
    set({ themeMode: mode });
  },
  cycleTheme: () => {
    const order: UIThemeMode[] = ['warm', 'arcade', 'cyber'];
    const current = get().themeMode;
    const next = order[(order.indexOf(current) + 1) % order.length];
    get().setThemeMode(next);
  },
}));
