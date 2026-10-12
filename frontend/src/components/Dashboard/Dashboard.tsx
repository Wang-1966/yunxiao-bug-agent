import React, { useEffect, useState } from 'react';
import { Button, Empty, Table, Tag, Progress, Drawer } from 'antd';
import { Link } from 'react-router-dom';
import {
  BugOutlined,
  RobotOutlined,
  CheckCircleOutlined,
} from '@ant-design/icons';
import { bugService } from '../../services/bugService';
import BugLink from '../Bugs/BugLink';
import BugDetail from '../Bugs/BugDetail';
import { agentService } from '../../services/agentService';
import type { Bug, AgentStatus } from '../../types';

const Dashboard: React.FC = () => {
  const [bugs, setBugs] = useState<Bug[]>([]);
  const [agents, setAgents] = useState<AgentStatus[]>([]);
  const [loading, setLoading] = useState(true);
  const [stats, setStats] = useState({
    total: 0,
    critical: 0,
    resolved: 0,
  });
  const [detailBugId, setDetailBugId] = useState<string | null>(null);

  useEffect(() => {
    loadData();
    const interval = setInterval(loadData, 5000); // 每 5 秒刷新
    return () => clearInterval(interval);
  }, []);

  const loadData = async () => {
    try {
      const [bugsData, agentsData, statsData] = await Promise.all([
        bugService.list({ page_size: 10, exclude_closed_yunxiao: true }),
        agentService.list(),
        bugService.dashboardStats(),
      ]);

      setBugs(bugsData.items || []);
      setAgents(agentsData || []);

      setStats(statsData);

      setLoading(false);
    } catch (error) {
      console.error('Failed to load dashboard data:', error);
      setLoading(false);
    }
  };

  const priorityColor = (priority: string) => {
    switch (priority) {
      case 'critical':
        return 'red';
      case 'important':
        return 'orange';
      case 'normal':
        return 'blue';
      default:
        return 'default';
    }
  };

  const statusColor = (status: string) => {
    switch (status) {
      case 'resolved':
        return 'success';
      case 'fixing':
        return 'processing';
      case 'failed':
        return 'error';
      default:
        return 'default';
    }
  };

  const agentColumns = [
    {
      title: 'Agent',
      dataIndex: 'name',
      key: 'name',
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      render: (status: string) => (
        <Tag color={status === 'busy' ? 'processing' : 'default'}>
          {status}
        </Tag>
      ),
    },
    {
      title: '当前任务',
      dataIndex: 'current_bug',
      key: 'current_bug',
      render: (bug: Bug | undefined) => bug ? <BugLink bug={bug} /> : '-',
    },
    {
      title: '进度',
      dataIndex: 'progress',
      key: 'progress',
      render: (progress: number) => <Progress percent={progress} size="small" />,
    },
  ];

  const recentBugsColumns = [
    {
      title: '标题',
      dataIndex: 'title',
      key: 'title',
      render: (_: string, bug: Bug) => <BugLink bug={bug} onClick={(event) => {
        if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        setDetailBugId(bug.bug_id);
      }} />,
    },
    {
      title: '优先级',
      dataIndex: 'priority',
      key: 'priority',
      render: (priority: string) => (
        <Tag color={priorityColor(priority)}>{priority}</Tag>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      render: (status: string) => (
        <Tag color={statusColor(status)}>{status}</Tag>
      ),
    },
  ];

  return (
    <div>
      <div className="page-heading">
        <div><div className="page-eyebrow">OVERVIEW</div><h1>仪表盘</h1></div>
        <Link to="/bugs"><Button icon={<BugOutlined />}>查看 Bug 列表</Button></Link>
      </div>

      {/* 统计卡片 */}
      <div className="metrics">
        <div className="metric"><div className="metric-label">总 Bug 数<BugOutlined className="metric-icon" /></div><div className="metric-value">{stats.total}</div><div className="metric-note">当前可见 Bug 总数</div></div>
        <div className="metric metric-critical"><div className="metric-label">关键 Bug<BugOutlined className="metric-icon" /></div><div className="metric-value">{stats.critical}</div><div className="metric-note">最高优先级</div></div>
        <div className="metric metric-resolved"><div className="metric-label">已解决<CheckCircleOutlined className="metric-icon" /></div><div className="metric-value">{stats.resolved}</div><div className="metric-note">Agent 修复并验收通过</div></div>
        <div className="metric metric-agents"><div className="metric-label">运行中 Agent<RobotOutlined className="metric-icon" /></div><div className="metric-value">{agents.filter((a) => a.status === 'busy').length}</div><div className="metric-note">正在执行任务</div></div>
      </div>

      {/* Agent 状态 */}
      <section className="data-section">
        <div className="section-heading"><h2>活跃 Agent</h2><Link to="/agents">查看全部</Link></div>
        <Table
          scroll={{ x: 540 }}
          locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无运行中的 Agent" /> }}
          dataSource={agents.filter((a) => a.status === 'busy')}
          columns={agentColumns}
          rowKey="agent_id"
          loading={loading}
          pagination={false}
        />
        <Drawer title="Bug 详情" placement="right" width="min(960px, 100vw)" maskClosable styles={{ mask: { background: 'transparent' } }} rootClassName="bug-detail-drawer" open={detailBugId !== null} onClose={() => setDetailBugId(null)} destroyOnClose>
          {detailBugId && <BugDetail bugId={detailBugId} embedded />}
        </Drawer>
      </section>

      {/* 最近的 Bug */}
      <section className="data-section">
        <div className="section-heading"><h2>最近的 Bug</h2><span>最近 10 条</span></div>
        <Table
          scroll={{ x: 540 }}
          locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无 Bug 记录" /> }}
          dataSource={bugs.slice(0, 10)}
          columns={recentBugsColumns}
          rowKey="bug_id"
          loading={loading}
          pagination={false}
        />
      </section>
    </div>
  );
};

export default Dashboard;
