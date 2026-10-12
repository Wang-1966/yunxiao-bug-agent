import React from 'react';
import { Layout, Menu } from 'antd';
import { Outlet, useNavigate, useLocation } from 'react-router-dom';
import {
  DashboardOutlined,
  BugOutlined,
  RobotOutlined,
  SettingOutlined,
  FolderOutlined,
} from '@ant-design/icons';

const { Header, Sider, Content } = Layout;

const AppLayout: React.FC = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const activePath = location.pathname.startsWith('/bugs/') ? '/bugs' : location.pathname;

  const menuItems = [
    {
      key: '/',
      icon: <DashboardOutlined />,
      label: '仪表盘',
    },
    {
      key: '/bugs',
      icon: <BugOutlined />,
      label: 'Bug 列表',
    },
    {
      key: '/agents',
      icon: <RobotOutlined />,
      label: 'Agent 状态',
    },
    {
      key: '/projects',
      icon: <FolderOutlined />,
      label: '项目管理',
    },
    {
      key: '/llm-config',
      icon: <SettingOutlined />,
      label: '通用配置',
    },
  ];

  return (
    <Layout className="app-shell">
      <a className="skip-link" href="#main-content">跳转到主要内容</a>
        <Sider width={224} className="app-sidebar">
          <div className="sidebar-inner">
            <div className="brand">
              <div className="brand-symbol"><BugOutlined /></div>
              <div><div className="brand-name">云效 Bug Agent</div><div className="brand-caption">Bug 修复工作台</div></div>
            </div>
            <div className="nav-label">工作空间</div>
          <Menu
            mode="inline"
            selectedKeys={[activePath]}
            items={menuItems}
            onClick={({ key }) => navigate(key)}
            style={{ border: 'none' }}
          />
            <div className="sidebar-footer"><strong>云效 Bug 修复 Agent</strong>版本 1.0.0</div>
          </div>
        </Sider>
      <Layout className="app-main">
        <Header className="app-topbar">
          <div className="topbar-location"><span>工作空间</span><span>/</span><strong>{location.pathname.startsWith('/bugs/') ? 'Bug 详情' : menuItems.find((item) => item.key === activePath)?.label}</strong></div>
          <span className="topbar-meta">云效 Bug 修复工作台</span>
        </Header>
        <Content className="app-content" id="main-content" tabIndex={-1}>
          <div className="app-content-inner">
            <Outlet />
          </div>
        </Content>
      </Layout>
    </Layout>
  );
};

export default AppLayout;
