import React, { useEffect, useState } from 'react';
import axios from 'axios';
import {
  Table,
  Tag,
  Progress,
  Button,
  message,
  Modal,
  Descriptions,
  Dropdown,
  Space,
  Drawer,
  Segmented,
} from 'antd';
import {
  PauseCircleOutlined,
  PlayCircleOutlined,
  StopOutlined,
  ReloadOutlined,
  DeleteOutlined,
  DownOutlined,
  DesktopOutlined,
  BarsOutlined,
} from '@ant-design/icons';
import { agentService } from '../../services/agentService';
import { bugService } from '../../services/bugService';
import BugLink from '../Bugs/BugLink';
import BugDetail from '../Bugs/BugDetail';
import RepairReview from './RepairReview';
import AgentTranscript from './AgentTranscript';
import { AgentWorkstationCard } from './AgentWorkstationCard';
import { AgentOffice } from './AgentOffice';
import { AgentAvatar } from '../Shared/AgentAvatar';
import type { AgentStatus, Bug } from '../../types';
import { getStatusLabel } from '../../utils/statusLabels';

const AgentList: React.FC = () => {
  const [agents, setAgents] = useState<AgentStatus[]>([]);
  const [loading, setLoading] = useState(false);
  const [retryingBugs, setRetryingBugs] = useState<string[]>([]);
  const [deletingAgents, setDeletingAgents] = useState<string[]>([]);
  const [stoppingAgents, setStoppingAgents] = useState<string[]>([]);
  const [reviewAgent, setReviewAgent] = useState<string | null>(null);
  const [detailBugId, setDetailBugId] = useState<string | null>(null);
  const [detailDirty, setDetailDirty] = useState(false);
  const [viewMode, setViewMode] = useState<'office' | 'table'>('table');
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<string>('all');

  useEffect(() => {
    loadAgents();
    const interval = setInterval(loadAgents, 3000);
    return () => clearInterval(interval);
  }, []);

  const loadAgents = async () => {
    setLoading(true);
    try {
      const data = await agentService.list();
      setAgents(data || []);
    } catch (error) {
      console.error('Failed to load agents:', error);
    } finally {
      setLoading(false);
    }
  };

  const handlePause = async (agentId: string) => {
    try {
      await agentService.pause(agentId);
      loadAgents();
    } catch (error) {
      console.error('Failed to pause agent:', error);
    }
  };

  const handleResume = async (agentId: string) => {
    try {
      await agentService.resume(agentId);
      loadAgents();
    } catch (error) {
      console.error('Failed to resume agent:', error);
    }
  };

  const handleStop = async (agentId: string) => {
    setStoppingAgents((ids) => [...ids, agentId]);
    try {
      await agentService.stop(agentId);
      message.success('任务已停止，可重新执行');
      await loadAgents();
    } catch (error) {
      message.error(
        axios.isAxiosError(error) ? error.response?.data?.detail || '停止失败' : '停止失败',
      );
    } finally {
      setStoppingAgents((ids) => ids.filter((id) => id !== agentId));
    }
  };

  const confirmDelete = (agent: AgentStatus) => {
    Modal.confirm({
      title: '删除 Agent？',
      content: `将停止“${agent.name}”的任务，并删除该 Agent 的日志和关联记录。Bug 和工作树中的代码修改会保留，可重新提交修复。`,
      okText: '删除',
      cancelText: '取消',
      okButtonProps: { danger: true },
      onOk: async () => {
        setDeletingAgents((ids) => [...ids, agent.agent_id]);
        try {
          await agentService.delete(agent.agent_id);
          setAgents((items) => items.filter((item) => item.agent_id !== agent.agent_id));
          setSelectedAgentId((id) => (id === agent.agent_id ? null : id));
          message.success('Agent 已删除，Bug 已保留');
          await loadAgents();
        } catch (error) {
          message.error(
            axios.isAxiosError(error) ? error.response?.data?.detail || '删除失败' : '删除失败',
          );
          throw error;
        } finally {
          setDeletingAgents((ids) => ids.filter((id) => id !== agent.agent_id));
        }
      },
    });
  };

  const handleRetry = async (agent: AgentStatus, fromScratch = false) => {
    const bug = agent.current_bug;
    if (!bug || retryingBugs.includes(bug.bug_id)) return;
    setRetryingBugs((ids) => [...ids, bug.bug_id]);
    try {
      if (fromScratch) {
        await bugService.submitTask({ bug_id: bug.bug_id, priority: 5 });
        message.success('已创建新的修复任务，将从头执行；原任务日志已保留');
      } else {
        const result = await agentService.retry(agent.agent_id);
        if (result.mode === 'resume')
          message.success(`已从「${result.stage_label}」继续执行（${result.reason}）`);
        else message.info(`${result.reason}，已创建新的修复任务从头执行；原任务记录已保留`);
      }
      await loadAgents();
    } catch (error) {
      message.error(
        axios.isAxiosError(error) ? error.response?.data?.detail || '重新执行失败' : '重新执行失败',
      );
    } finally {
      setRetryingBugs((ids) => ids.filter((id) => id !== bug.bug_id));
    }
  };

  const changeDetails = (bugId: string | null) => {
    const change = () => {
      setDetailBugId(bugId);
      setDetailDirty(false);
    };
    if (bugId === detailBugId) return;
    if (detailDirty)
      Modal.confirm({
        title: '补充信息尚未保存',
        content: '离开当前 Bug 将丢弃修改。',
        okText: '丢弃并离开',
        cancelText: '继续编辑',
        onOk: change,
      });
    else change();
  };

  const statusColor = (status: string) => {
    switch (status) {
      case 'busy':
        return 'processing';
      case 'idle':
        return 'success';
      case 'paused':
      case 'awaiting_confirmation':
      case 'awaiting_plan_confirmation':
        return 'warning';
      case 'error':
        return 'error';
      default:
        return 'default';
    }
  };

  const matchesFilter = (agent: AgentStatus) => {
    if (statusFilter === 'busy') return agent.status === 'busy';
    if (statusFilter === 'waiting')
      return ['awaiting_plan_confirmation', 'awaiting_confirmation'].includes(agent.status);
    if (statusFilter === 'error') return agent.status === 'error';
    return true;
  };
  const filteredAgents = agents.filter(matchesFilter);
  const selectedAgent = agents.find((agent) => agent.agent_id === selectedAgentId);

  const columns = [
    {
      title: 'Agent 特工',
      dataIndex: 'name',
      key: 'name',
      render: (name: string, record: AgentStatus) => (
        <Space size={10}>
          <AgentAvatar
            name={name}
            status={record.status}
            stage={record.current_stage}
            size="sm"
          />
          <strong>{name}</strong>
        </Space>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      render: (status: string) => <Tag color={statusColor(status)}>{getStatusLabel(status)}</Tag>,
    },
    {
      title: '当前任务',
      dataIndex: 'current_bug',
      key: 'current_bug',
      render: (bug: Bug | undefined) =>
        bug ? (
          <BugLink
            bug={bug}
            onClick={(event) => {
              if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
              event.preventDefault();
              changeDetails(bug.bug_id);
            }}
          />
        ) : (
          '-'
        ),
    },
    {
      title: '当前阶段',
      dataIndex: 'current_stage',
      key: 'current_stage',
      render: (stage?: string) => getStatusLabel(stage),
    },
    {
      title: '进度',
      dataIndex: 'progress',
      key: 'progress',
      render: (progress: number) => <Progress percent={progress} size="small" status="active" />,
    },
    {
      title: '操作',
      key: 'action',
      width: 370,
      render: (_: any, record: AgentStatus) => (
        <div className="agent-row-actions">
          <div>
            {record.worktree_path && (
              <Button
                size="small"
                type={
                  ['awaiting_confirmation', 'awaiting_plan_confirmation'].includes(record.status)
                    ? 'primary'
                    : 'default'
                }
                onClick={() => setReviewAgent(record.agent_id)}
              >
                {record.status === 'awaiting_plan_confirmation'
                  ? '审查修复方案'
                  : record.status === 'awaiting_confirmation'
                    ? '确认修复结果'
                    : '查看修复结果'}
              </Button>
            )}
          </div>
          <div className="agent-task-controls">
            {record.status === 'error' &&
              (() => {
                const retrying =
                  !!record.current_bug && retryingBugs.includes(record.current_bug.bug_id);
                const bug = record.current_bug;
                const claimable =
                  !!bug &&
                  (bug.assigned_agent_id === record.agent_id
                    ? bug.status === 'failed'
                    : !bug.assigned_agent_id && ['pending', 'failed'].includes(bug.status));
                const disabled =
                  retrying ||
                  !claimable ||
                  ['已修复', '已关闭', '暂不修复'].includes(bug?.source_status || '');
                return (
                  <Space.Compact size="small">
                    <Button
                      size="small"
                      icon={<ReloadOutlined />}
                      loading={retrying}
                      disabled={disabled}
                      title="从失败的阶段继续，复用已有工作树和分析结果"
                      onClick={() => handleRetry(record)}
                    >
                      重新执行
                    </Button>
                    <Dropdown
                      disabled={disabled}
                      trigger={['click']}
                      menu={{
                        items: [{ key: 'scratch', label: '从头重新执行（新建任务）' }],
                        onClick: () => handleRetry(record, true),
                      }}
                    >
                      <Button size="small" icon={<DownOutlined />} disabled={disabled} />
                    </Dropdown>
                  </Space.Compact>
                );
              })()}
            {record.status === 'busy' && (
              <>
                <Button
                  size="small"
                  icon={<PauseCircleOutlined />}
                  onClick={() => handlePause(record.agent_id)}
                >
                  暂停
                </Button>
                <Button
                  size="small"
                  danger
                  icon={<StopOutlined />}
                  loading={stoppingAgents.includes(record.agent_id)}
                  onClick={() => handleStop(record.agent_id)}
                >
                  停止
                </Button>
              </>
            )}
            {record.status === 'paused' && (
              <Button
                size="small"
                type="primary"
                icon={<PlayCircleOutlined />}
                onClick={() => handleResume(record.agent_id)}
              >
                恢复
              </Button>
            )}
          </div>
          <Button
            size="small"
            danger
            icon={<DeleteOutlined />}
            loading={deletingAgents.includes(record.agent_id)}
            disabled={stoppingAgents.includes(record.agent_id)}
            onClick={() => confirmDelete(record)}
          >
            删除
          </Button>
        </div>
      ),
    },
  ];

  const expandedRowRender = (record: AgentStatus) => {
    return (
      <div className="agent-logs">
        {record.worktree_path && (
          <Descriptions
            title="修复结果"
            column={1}
            items={[
              { key: 'worktree', label: '修改保存位置', children: record.worktree_path },
              {
                key: 'branch',
                label: '修复分支',
                children: record.repair_branch || '可在工作树中查看',
              },
              {
                key: 'files',
                label: '修改文件',
                children: record.modified_files?.join('、') || '可在工作树中查看 Git diff',
              },
            ]}
          />
        )}
        <h2>Agent 过程</h2>
        <AgentTranscript agentId={record.agent_id} active={record.status === 'busy'} />
      </div>
    );
  };

  return (
    <div>
      <div className="page-heading">
        <div>
          <div className="page-eyebrow">🤖 AGENT WORKSTATIONS</div>
          <h1>Agent 工位实况与闯关监控</h1>
          <p className="page-subtitle">
            每位特工拥有独立 Git Worktree 工位，实时展示思考流、五关修复进度与待拍板方案
          </p>
        </div>

        <Space wrap>
          <Segmented
            value={statusFilter}
            onChange={(val) => setStatusFilter(String(val))}
            options={[
              { value: 'all', label: `全部工位 (${agents.length})` },
              {
                value: 'busy',
                label: `⚡ 作战中 (${agents.filter((a) => a.status === 'busy').length})`,
              },
              {
                value: 'waiting',
                label: `📋 待你拍板 (${
                  agents.filter((a) =>
                    ['awaiting_plan_confirmation', 'awaiting_confirmation'].includes(a.status),
                  ).length
                })`,
              },
              {
                value: 'error',
                label: `💫 异常踩坑 (${agents.filter((a) => a.status === 'error').length})`,
              },
            ]}
          />
          <Segmented
            value={viewMode}
            onChange={(val) => setViewMode(val as 'office' | 'table')}
            options={[
              { value: 'office', icon: <DesktopOutlined />, label: '办公室' },
              { value: 'table', icon: <BarsOutlined />, label: '紧凑表格' },
            ]}
          />
        </Space>
      </div>

      <section className="data-section">
        {viewMode === 'office' ? (
          <AgentOffice
            agents={agents}
            isHighlighted={matchesFilter}
            selectedId={selectedAgentId}
            onSelect={setSelectedAgentId}
            onReview={setReviewAgent}
          />
        ) : (
          <Table
            scroll={{ x: 1070 }}
            columns={columns}
            dataSource={filteredAgents}
            rowKey="agent_id"
            loading={loading}
            expandable={{
              expandedRowRender,
              rowExpandable: (record) => record.logs && record.logs.length > 0,
            }}
            pagination={false}
          />
        )}
      </section>

      <Drawer
        title={selectedAgent ? `${selectedAgent.name} 的工位` : 'Agent 工位'}
        placement="right"
        width="min(720px, 100vw)"
        zIndex={990}
        rootClassName="agent-workstation-drawer"
        open={!!selectedAgent}
        onClose={() => setSelectedAgentId(null)}
        destroyOnClose
      >
        {selectedAgent && (
          <AgentWorkstationCard
            key={selectedAgent.agent_id}
            agent={selectedAgent}
            retrying={
              !!selectedAgent.current_bug && retryingBugs.includes(selectedAgent.current_bug.bug_id)
            }
            stopping={stoppingAgents.includes(selectedAgent.agent_id)}
            deleting={deletingAgents.includes(selectedAgent.agent_id)}
            onOpenBug={(bugId) => changeDetails(bugId)}
            onReview={(agentId) => setReviewAgent(agentId)}
            onRetry={(target, scratch) => void handleRetry(target, scratch)}
            onPause={(id) => void handlePause(id)}
            onResume={(id) => void handleResume(id)}
            onStop={(id) => void handleStop(id)}
            onDelete={(target) => confirmDelete(target)}
          />
        )}
      </Drawer>
      <Drawer
        title="Bug 详情"
        placement="right"
        width="min(960px, 100vw)"
        maskClosable
        styles={{ mask: { background: 'transparent' } }}
        rootClassName="bug-detail-drawer"
        open={detailBugId !== null}
        onClose={() => changeDetails(null)}
        destroyOnClose
      >
        {detailBugId && (
          <BugDetail
            bugId={detailBugId}
            embedded
            onDirtyChange={setDetailDirty}
            onStarted={() => void loadAgents()}
          />
        )}
      </Drawer>
      <RepairReview
        agentId={reviewAgent}
        onClose={() => setReviewAgent(null)}
        onConfirmed={() => void loadAgents()}
      />
    </div>
  );
};

export default AgentList;
