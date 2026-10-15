import React, { useEffect, useState } from 'react';
import { Button, Empty, Table, Tag, Drawer, Space } from 'antd';
import { Link } from 'react-router-dom';
import {
  BugOutlined,
  RobotOutlined,
  CheckCircleOutlined,
  FireOutlined,
} from '@ant-design/icons';
import { bugService } from '../../services/bugService';
import BugLink from '../Bugs/BugLink';
import BugDetail from '../Bugs/BugDetail';
import RepairReview from '../Agents/RepairReview';
import { AgentWorkstationCard } from '../Agents/AgentWorkstationCard';
import { agentService } from '../../services/agentService';
import { getStatusLabel } from '../../utils/statusLabels';
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
  const [reviewAgent, setReviewAgent] = useState<string | null>(null);

  useEffect(() => {
    loadData();
    const interval = setInterval(loadData, 5000);
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

  const priorityRender = (priority: string) => {
    if (priority === 'critical') {
      return <span className="bounty-badge bounty-critical">🔥 S级 · 关键</span>;
    }
    if (priority === 'important') {
      return <span className="bounty-badge bounty-important">⚡ A级 · 重要</span>;
    }
    return <span className="bounty-badge bounty-normal">📌 B级 · 普通</span>;
  };

  const statusColor = (status: string) => {
    switch (status) {
      case 'resolved':
        return 'success';
      case 'awaiting_plan_confirmation':
      case 'awaiting_confirmation':
        return 'warning';
      case 'fixing':
      case 'analyzing':
      case 'testing':
        return 'processing';
      case 'failed':
        return 'error';
      default:
        return 'default';
    }
  };

  const activeAgents = agents.filter((a) =>
    ['busy', 'awaiting_plan_confirmation', 'awaiting_confirmation', 'paused'].includes(a.status),
  );

  const recentBugsColumns = [
    {
      title: '悬赏标题',
      dataIndex: 'title',
      key: 'title',
      render: (_: string, bug: Bug) => (
        <BugLink
          bug={bug}
          onClick={(event) => {
            if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
            event.preventDefault();
            setDetailBugId(bug.bug_id);
          }}
        />
      ),
    },
    {
      title: '悬赏等级',
      dataIndex: 'priority',
      key: 'priority',
      width: 140,
      render: (priority: string) => priorityRender(priority),
    },
    {
      title: '云效项目',
      dataIndex: 'source_project',
      key: 'source_project',
      width: 180,
      render: (project?: string) => project || '-',
    },
    {
      title: '当前状态',
      dataIndex: 'status',
      key: 'status',
      width: 140,
      render: (status: string) => <Tag color={statusColor(status)}>{getStatusLabel(status)}</Tag>,
    },
  ];

  return (
    <div>
      <div className="page-heading">
        <div>
          <div className="page-eyebrow">⚡ SQUAD COMMAND CENTER</div>
          <h1>仪表盘 · 作战室</h1>
          <p className="page-subtitle">
            实时总览云效悬赏池、特工队工位闯关进度与战果统计
          </p>
        </div>
        <Space wrap>
          <Link to="/agents">
            <Button icon={<RobotOutlined />}>巡视 Agent 工位</Button>
          </Link>
          <Link to="/bugs">
            <Button type="primary" icon={<BugOutlined />}>
              前往 Bug 悬赏榜
            </Button>
          </Link>
        </Space>
      </div>

      {/* 趣味战报卡片 */}
      <div className="metrics">
        <div className="metric">
          <div className="metric-label">
            悬赏池总 Bug 数
            <span className="metric-icon">
              <BugOutlined />
            </span>
          </div>
          <div className="metric-value">{stats.total}</div>
          <div className="metric-note">当前待捕获与处理中的可见 Bug</div>
        </div>

        <div className="metric metric-critical">
          <div className="metric-label">
            S 级关键悬赏
            <span className="metric-icon">
              <FireOutlined />
            </span>
          </div>
          <div className="metric-value">{stats.critical}</div>
          <div className="metric-note">最高优先级，建议优先派单</div>
        </div>

        <div className="metric metric-resolved">
          <div className="metric-label">
            已通关消灭
            <span className="metric-icon">
              <CheckCircleOutlined />
            </span>
          </div>
          <div className="metric-value">{stats.resolved}</div>
          <div className="metric-note">Agent 修复并人工验收通过</div>
        </div>

        <div className="metric metric-agents">
          <div className="metric-label">
            在线作战 Agent
            <span className="metric-icon">
              <RobotOutlined />
            </span>
          </div>
          <div className="metric-value">{activeAgents.length}</div>
          <div className="metric-note">
            {agents.filter((a) =>
              ['awaiting_plan_confirmation', 'awaiting_confirmation'].includes(a.status),
            ).length > 0
              ? `其中 ${
                  agents.filter((a) =>
                    ['awaiting_plan_confirmation', 'awaiting_confirmation'].includes(a.status),
                  ).length
                } 位正等待你拍板验收`
              : '独立 Git Worktree 并发闯关中'}
          </div>
        </div>
      </div>

      {/* 活跃 Agent 工位实况卡片 */}
      <section className="data-section">
        <div className="section-heading">
          <h2>活跃 Agent 工位实况</h2>
          <Link to="/agents">查看全部工位 →</Link>
        </div>

        {activeAgents.length > 0 ? (
          <div className="workstation-grid is-compact">
            {activeAgents.map((agent) => (
              <AgentWorkstationCard
                key={agent.agent_id}
                agent={agent}
                compact
                onOpenBug={(bugId) => setDetailBugId(bugId)}
                onReview={(agentId) => setReviewAgent(agentId)}
              />
            ))}
          </div>
        ) : (
          <div className="fun-empty-box">
            <div className="fun-empty-illustration">☕🦉</div>
            <div className="fun-empty-title">当前暂无运行中的 Agent，特工们正在喝咖啡待命</div>
            <div className="fun-empty-desc">
              前往 <Link to="/bugs">Bug 悬赏榜</Link> 点击「修复」，即可唤醒夜猫子修修、咖啡因大夫等特工出击！
            </div>
          </div>
        )}

        <Drawer
          title="Bug 详情"
          placement="right"
          width="min(960px, 100vw)"
          maskClosable
          styles={{ mask: { background: 'transparent' } }}
          rootClassName="bug-detail-drawer"
          open={detailBugId !== null}
          onClose={() => setDetailBugId(null)}
          destroyOnClose
        >
          {detailBugId && <BugDetail bugId={detailBugId} embedded onStarted={() => void loadData()} />}
        </Drawer>
        <RepairReview
          agentId={reviewAgent}
          onClose={() => setReviewAgent(null)}
          onConfirmed={() => void loadData()}
        />
      </section>

      {/* 最近的 Bug */}
      <section className="data-section">
        <div className="section-heading">
          <h2>最新悬赏情报</h2>
          <span>最近 10 条未关闭记录</span>
        </div>
        <Table
          scroll={{ x: 640 }}
          locale={{
            emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无 Bug 记录" />,
          }}
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
