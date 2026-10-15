import React, { useEffect } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { ConfigProvider, theme } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import AppLayout from './components/Layout/AppLayout';
import Dashboard from './components/Dashboard/Dashboard';
import BugList from './components/Bugs/BugList';
import BugDetail from './components/Bugs/BugDetail';
import AgentList from './components/Agents/AgentList';
import LLMConfigPage from './components/LLMConfig/LLMConfigPage';
import ProjectList from './components/Projects/ProjectList';
import { useWebSocket } from './hooks/useWebSocket';
import { useThemeStore, type UIThemeMode } from './stores/themeStore';

function buildAntdTheme(mode: UIThemeMode) {
  if (mode === 'cyber') {
    return {
      algorithm: theme.darkAlgorithm,
      token: {
        colorPrimary: '#38BDF8',
        colorInfo: '#38BDF8',
        colorSuccess: '#34D399',
        colorWarning: '#FBBF24',
        colorError: '#F87171',
        colorText: '#E2E8F0',
        colorTextSecondary: '#94A3B8',
        colorBgLayout: '#0B111E',
        colorBgContainer: '#131C2E',
        colorBgElevated: '#1A263D',
        colorBorderSecondary: '#23324D',
        borderRadius: 10,
        controlHeight: 36,
        fontFamily:
          "'JetBrains Mono', -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', sans-serif",
      },
      components: {
        Layout: { siderBg: '#0F172A' },
        Menu: {
          itemBg: '#0F172A',
          itemColor: '#94A3B8',
          itemSelectedBg: 'rgba(56, 189, 248, 0.16)',
          itemSelectedColor: '#38BDF8',
          itemHoverBg: 'rgba(56, 189, 248, 0.08)',
          itemHoverColor: '#F8FAFC',
        },
        Table: { headerBg: '#172238', cellPaddingBlock: 14 },
      },
    };
  }

  if (mode === 'arcade') {
    return {
      algorithm: theme.defaultAlgorithm,
      token: {
        colorPrimary: '#E85D3F',
        colorInfo: '#E85D3F',
        colorText: '#181512',
        colorTextSecondary: '#574E46',
        colorBgLayout: '#FFFDF7',
        colorBgContainer: '#FFFFFF',
        colorBorderSecondary: '#181512',
        borderRadius: 6,
        controlHeight: 36,
        fontFamily:
          "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif",
      },
      components: {
        Layout: { siderBg: '#FFF8E7' },
        Menu: {
          itemBg: '#FFF8E7',
          itemColor: '#4A4036',
          itemSelectedBg: '#FFE2B8',
          itemSelectedColor: '#181512',
          itemHoverBg: '#FFF0D4',
          itemHoverColor: '#181512',
        },
        Table: { headerBg: '#FFF4D6', cellPaddingBlock: 14 },
        Button: {
          primaryColor: '#FFFFFF',
          colorPrimaryHover: '#D1482B',
          colorPrimaryActive: '#B5381E',
        },
      },
    };
  }

  // 默认：暖调捉虫工坊 (warm)
  return {
    algorithm: theme.defaultAlgorithm,
    token: {
      colorPrimary: '#DA7756',
      colorInfo: '#DA7756',
      colorText: '#1F1915',
      colorTextSecondary: '#6B5D52',
      colorBgLayout: '#F4F3EE',
      colorBgContainer: '#FFFFFF',
      colorBorderSecondary: '#E5E0D8',
      borderRadius: 10,
      controlHeight: 36,
      fontFamily:
        "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif",
    },
    components: {
      Layout: { siderBg: '#FFFFFF' },
      Menu: {
        itemBg: '#FFFFFF',
        itemColor: '#6B5D52',
        itemSelectedBg: '#FCF0EB',
        itemSelectedColor: '#DA7756',
        itemHoverBg: '#F9F4F0',
        itemHoverColor: '#1F1915',
      },
      Table: { headerBg: '#FAF9F5', cellPaddingBlock: 16 },
      Button: {
        primaryColor: '#FFFFFF',
        colorPrimaryHover: '#C4553D',
        colorPrimaryActive: '#B24A35',
      },
    },
  };
}

const App: React.FC = () => {
  useWebSocket();
  const themeMode = useThemeStore((state) => state.themeMode);

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', themeMode);
  }, [themeMode]);

  return (
    <ConfigProvider locale={zhCN} theme={buildAntdTheme(themeMode)}>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<AppLayout />}>
            <Route index element={<Dashboard />} />
            <Route path="bugs" element={<BugList />} />
            <Route path="bugs/:bugId" element={<BugDetail />} />
            <Route path="agents" element={<AgentList />} />
            <Route path="projects" element={<ProjectList />} />
            <Route path="llm-config" element={<LLMConfigPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </ConfigProvider>
  );
};

export default App;
