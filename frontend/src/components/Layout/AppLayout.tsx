import React from 'react';
import { Breadcrumb, Layout, Menu, Segmented, Tooltip } from 'antd';
import { Link, Outlet, useNavigate, useLocation } from 'react-router-dom';
import {
  DashboardOutlined,
  BugOutlined,
  RobotOutlined,
  SettingOutlined,
  FolderOutlined,
  SafetyOutlined,
} from '@ant-design/icons';
import { UI_THEMES, useThemeStore, type UIThemeMode } from '../../stores/themeStore';

const { Header, Sider, Content } = Layout;

const AppLayout: React.FC = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const isWorkspace = location.pathname.startsWith('/agents/');
  const activePath = isWorkspace ? '/agents' : location.pathname.startsWith('/bugs/') ? '/bugs' : location.pathname;
  const { themeMode, setThemeMode } = useThemeStore();
  const currentTheme = UI_THEMES[themeMode];

  const menuItems = [
    {
      key: '/',
      icon: <DashboardOutlined />,
      label: '仪表盘',
    },
    {
      key: '/bugs',
      icon: <BugOutlined />,
      label: 'Bug 悬赏榜',
    },
    {
      key: '/agents',
      icon: <RobotOutlined />,
      label: 'Agent 工位实况',
    },
    {
      key: '/projects',
      icon: <FolderOutlined />,
      label: '项目与仓库',
    },
    {
      key: '/llm-config',
      icon: <SettingOutlined />,
      label: '模型与通用配置',
    },
    {
      key: '/confirmation-settings',
      icon: <SafetyOutlined />,
      label: '方案确认策略',
    },
  ];

  return (
    <Layout className={`app-shell theme-${themeMode}${isWorkspace ? ' workspace-shell' : ''}`}>
      <a className="skip-link" href="#main-content">
        跳转到主要内容
      </a>
      <Sider width={240} className="app-sidebar">
        <div className="sidebar-inner">
          <div className="brand" onClick={() => navigate('/')} style={{ cursor: 'pointer' }}>
            <div className="brand-symbol">
              <BugOutlined />
              <span className="brand-spark">✦</span>
            </div>
            <div>
              <div className="brand-name">Self-Healing Agent Yunxiao</div>
              <div className="brand-caption">Multi-Agent 悬赏修复台</div>
            </div>
          </div>

          <div className="nav-label">工作空间</div>
          <Menu
            mode="inline"
            selectedKeys={[activePath]}
            items={menuItems}
            onClick={({ key }) => navigate(key)}
            style={{ border: 'none' }}
          />

          <div className="sidebar-footer">
            <strong>Self-Healing Agent Yunxiao</strong>
            <span>{currentTheme.name} · v1.0.0</span>
          </div>
        </div>
      </Sider>

      <Layout className="app-main">
        {!isWorkspace && <Header className="app-topbar">
          <Breadcrumb
            className="topbar-location"
            items={[
              { title: <Link to="/">工作空间</Link> },
              ...(location.pathname.startsWith('/bugs/')
                ? [{ title: <Link to="/bugs">Bug 悬赏榜</Link> }, { title: 'Bug 详情与补充' }]
                : [{ title: menuItems.find((item) => item.key === activePath)?.label }]),
            ]}
          />

          <div className="topbar-right">
            <Tooltip title={`当前风格：${currentTheme.tagline}`}>
              <div className="theme-switcher-wrap">
                <Segmented
                  size="small"
                  aria-label="切换界面视觉风格"
                  value={themeMode}
                  onChange={(val) => setThemeMode(val as UIThemeMode)}
                  options={Object.values(UI_THEMES).map((t) => ({
                    value: t.id,
                    label: t.badge,
                  }))}
                />
              </div>
            </Tooltip>
          </div>
        </Header>}

        <Content className={`app-content${isWorkspace ? ' workspace-content' : ''}`} id="main-content" tabIndex={-1}>
          <div className="app-content-inner">
            <Outlet />
          </div>
        </Content>
      </Layout>
    </Layout>
  );
};

export default AppLayout;
