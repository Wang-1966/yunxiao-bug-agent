import React from 'react';
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

const App: React.FC = () => {
  // 连接 WebSocket
  useWebSocket();

  return (
    <ConfigProvider
      locale={zhCN}
      theme={{
        algorithm: theme.defaultAlgorithm,
        token: {
          colorPrimary: '#DA7756',
          colorInfo: '#DA7756',
          colorText: '#1F1915',
          colorTextSecondary: '#6B5D52',
          colorBgLayout: '#F4F3EE',
          colorBorderSecondary: '#E5E0D8',
          borderRadius: 8,
          controlHeight: 36,
          fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif",
        },
        components: {
          Layout: { siderBg: '#FFFFFF' },
          Menu: {
            itemBg: '#FFFFFF',
            itemColor: '#6B5D52',
            itemSelectedBg: '#FCF0EB',
            itemSelectedColor: '#DA7756',
            itemHoverBg: '#F9F4F0',
            itemHoverColor: '#1F1915'
          },
          Table: { headerBg: '#FAF9F5', cellPaddingBlock: 16 },
          Button: {
            primaryColor: '#FFFFFF',
            colorPrimaryHover: '#C4553D',
            colorPrimaryActive: '#B24A35'
          },
        },
      }}
    >
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
