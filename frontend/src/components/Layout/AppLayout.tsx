import React from 'react';
import { Layout, Menu, Segmented, Tooltip } from 'antd';
import { Outlet, useNavigate, useLocation } from 'react-router-dom';
import {
  DashboardOutlined,
  BugOutlined,
  RobotOutlined,
  SettingOutlined,
  FolderOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import { UI_THEMES, useThemeStore, type UIThemeMode } from '../../stores/themeStore';

const { Header, Sider, Content } = Layout;

const AppLayout: React.FC = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const activePath = location.pathname.startsWith('/bugs/') ? '/bugs' : location.pathname;
  const { themeMode, setThemeMode } = useThemeStore();
  const currentTheme = UI_THEMES[themeMode];

  const menuItems = [
    {
      key: '/',
      icon: <DashboardOutlined />,
      label: '仪表盘 · 作战室',
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
  ];

  return (
    <Layout className={`app-shell theme-${themeMode}`}>
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
              <div className="brand-name">云效 Bug Agent</div>
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

          <div className="sidebar-squad-card">
            <div className="squad-card-title">
              <ThunderboltOutlined /> 8 位修复特工在线
            </div>
            <p className="squad-card-desc">
              夜猫子修修、咖啡因大夫、通宵啄木鸟等性格迥异的 Agent 在独立 Git Worktree 中并肩作战。
            </p>
          </div>

          <div className="sidebar-footer">
            <strong>云效 Bug 修复 Agent</strong>
            <span>{currentTheme.name} · v1.0.0</span>
          </div>
        </div>
      </Sider>

      <Layout className="app-main">
        <Header className="app-topbar">
          <div className="topbar-location">
            <span>工作空间</span>
            <span>/</span>
            <strong>
              {location.pathname.startsWith('/bugs/')
                ? 'Bug 详情与补充'
                : menuItems.find((item) => item.key === activePath)?.label}
            </strong>
          </div>

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
