import React, { useEffect, useState } from 'react';
import axios from 'axios';
import { Table, Tag, Progress, Button, Timeline, message, Modal, Descriptions } from 'antd';
import { PauseCircleOutlined, PlayCircleOutlined, StopOutlined, ReloadOutlined, DeleteOutlined } from '@ant-design/icons';
import { agentService } from '../../services/agentService';
import { bugService } from '../../services/bugService';
import BugLink from '../Bugs/BugLink';
import RepairReview from './RepairReview';
import type { AgentStatus, Bug } from '../../types';
import { getStatusLabel } from '../../utils/statusLabels';

const AgentList: React.FC = () => {
  const [agents, setAgents] = useState<AgentStatus[]>([]);
  const [loading, setLoading] = useState(false);
  const [retryingBugs, setRetryingBugs] = useState<string[]>([]);
  const [deletingAgents, setDeletingAgents] = useState<string[]>([]);
  const [stoppingAgents, setStoppingAgents] = useState<string[]>([]);
  const [reviewAgent, setReviewAgent] = useState<string | null>(null);

  useEffect(() => {
    loadAgents();
    const interval = setInterval(loadAgents, 3000); // 每 3 秒刷新
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
      message.error(axios.isAxiosError(error) ? error.response?.data?.detail || '停止失败' : '停止失败');
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
          message.success('Agent 已删除，Bug 已保留');
          await loadAgents();
        } catch (error) {
          message.error(axios.isAxiosError(error) ? error.response?.data?.detail || '删除失败' : '删除失败');
          throw error;
        } finally {
          setDeletingAgents((ids) => ids.filter((id) => id !== agent.agent_id));
        }
      },
    });
  };

  const handleRetry = async (bug: Bug) => {
    if (retryingBugs.includes(bug.bug_id)) return;
    setRetryingBugs((ids) => [...ids, bug.bug_id]);
    try {
      await bugService.submitTask({ bug_id: bug.bug_id, priority: 5 });
      setAgents((items) => items.map((agent) =>
        agent.current_bug?.bug_id === bug.bug_id
          ? { ...agent, current_bug: { ...agent.current_bug, status: 'analyzing' } }
          : agent
      ));
      message.success('已创建新的修复任务，将从头执行；原任务日志已保留');
      await loadAgents();
    } catch (error) {
      message.error(axios.isAxiosError(error) ? error.response?.data?.detail || '重新执行失败' : '重新执行失败');
    } finally {
      setRetryingBugs((ids) => ids.filter((id) => id !== bug.bug_id));
    }
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

  const columns = [
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
        <Tag color={statusColor(status)}>{getStatusLabel(status)}</Tag>
      ),
    },
    {
      title: '当前任务',
      dataIndex: 'current_bug',
      key: 'current_bug',
      render: (bug: Bug | undefined) => bug ? <BugLink bug={bug} /> : '-',
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
      render: (progress: number) => (
        <Progress percent={progress} size="small" status="active" />
      ),
    },
    {
      title: '操作',
      key: 'action',
      width: 370,
      render: (_: any, record: AgentStatus) => (
        <div className="agent-row-actions">
          <div>
          {record.worktree_path && <Button size="small" type={['awaiting_confirmation', 'awaiting_plan_confirmation'].includes(record.status) ? 'primary' : 'default'} onClick={() => setReviewAgent(record.agent_id)}>
            {record.status === 'awaiting_plan_confirmation' ? '审查修复方案' : record.status === 'awaiting_confirmation' ? '确认修复结果' : '查看修复结果'}
          </Button>}
          </div>
          <div className="agent-task-controls">
          {record.status === 'error' && (
            <Button
              size="small"
              icon={<ReloadOutlined />}
              loading={!!record.current_bug && retryingBugs.includes(record.current_bug.bug_id)}
              disabled={!record.current_bug || record.current_bug.status !== 'failed' || ['已修复', '已关闭', '暂不修复'].includes(record.current_bug.source_status || '')}
              onClick={() => record.current_bug && handleRetry(record.current_bug)}
            >
              重新执行
            </Button>
          )}
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
        {record.worktree_path && <Descriptions title="修复结果" column={1} items={[
          { key: 'worktree', label: '修改保存位置', children: record.worktree_path },
          { key: 'branch', label: '修复分支', children: record.repair_branch || '可在工作树中查看' },
          { key: 'files', label: '修改文件', children: record.modified_files?.join('、') || '可在工作树中查看 Git diff' },
        ]} />}
        <h2>最近日志</h2>
        <Timeline
          items={record.logs.slice(0, 10).map((log) => ({
            children: log,
          }))}
        />
      </div>
    );
  };

  return (
    <div>
      <div className="page-heading"><div><div className="page-eyebrow">AGENT MONITOR</div><h1>Agent 状态</h1></div></div>

      <section className="data-section">
        <Table
          scroll={{ x: 1070 }}
          columns={columns}
          dataSource={agents}
          rowKey="agent_id"
          loading={loading}
          expandable={{
            expandedRowRender,
            rowExpandable: (record) => record.logs && record.logs.length > 0,
          }}
          pagination={false}
        />
      </section>
      <RepairReview agentId={reviewAgent} onClose={() => setReviewAgent(null)} onConfirmed={() => void loadAgents()} />
    </div>
  );
};

export default AgentList;
