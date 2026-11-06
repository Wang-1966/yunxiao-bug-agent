import React, { useEffect, useState } from 'react';
import axios from 'axios';
import { Link } from 'react-router-dom';
import {
  Table,
  Tag,
  Button,
  Input,
  Select,
  Modal,
  Form,
  message,
  Drawer,
} from 'antd';
import { PlusOutlined, SyncOutlined, PlayCircleOutlined, ExportOutlined } from '@ant-design/icons';
import { bugService } from '../../services/bugService';
import BugLink from './BugLink';
import BugDetail from './BugDetail';
import RepairReview from '../Agents/RepairReview';
import type { Bug } from '../../types';
import { getStatusLabel, repairStatusLabels, sourceStatusClass } from '../../utils/statusLabels';

const { Search } = Input;
const { Option } = Select;
const HIDDEN_YUNXIAO = ['已修复', '已关闭', '暂不修复'];

function errorDetail(error: unknown, fallback: string) {
  const detail = axios.isAxiosError(error) ? error.response?.data?.detail : undefined;
  return typeof detail === 'string' ? detail : fallback;
}

function YunxiaoStatusCell({ bug, onChanged }: { bug: Bug; onChanged: (status: string) => void }) {
  const current = bug.source_status || '';
  const [options, setOptions] = useState<string[]>(current ? [current] : []);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  if (bug.source !== 'yunxiao') return <>-</>;
  return (
    <span className={sourceStatusClass(current || '其他')}>
      <Select
        className="yunxiao-status-select"
        variant="borderless"
        size="small"
        aria-label={`修改云效状态：${bug.title}`}
        value={current || undefined}
        placeholder="选择状态"
        popupMatchSelectWidth={false}
        loading={busy}
        open={open}
        options={options.map((status) => ({ value: status, label: status }))}
        onDropdownVisibleChange={async (visible) => {
          setOpen(visible);
          if (!visible || options.length > 1) return;
          setBusy(true);
          try {
            const names = await bugService.yunxiaoStatuses(bug.bug_id);
            setOptions(current && !names.includes(current) ? [current, ...names] : names);
          } catch (error) {
            message.error(errorDetail(error, '读取云效状态失败'));
            setOpen(false);
          } finally {
            setBusy(false);
          }
        }}
        onChange={async (status) => {
          if (status === current) return;
          setBusy(true);
          try {
            const result = await bugService.updateYunxiaoStatus(bug.bug_id, status);
            onChanged(result.source_status);
            message.success(`云效状态已改为${result.source_status}`);
          } catch (error) {
            message.error(errorDetail(error, '修改云效状态失败'));
          } finally {
            setBusy(false);
          }
        }}
      />
    </span>
  );
}

const BugList: React.FC = () => {
  const [bugs, setBugs] = useState<Bug[]>([]);
  const [loading, setLoading] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [submitting, setSubmitting] = useState<string[]>([]);
  const [total, setTotal] = useState(0);
  const [sourceStatuses, setSourceStatuses] = useState<string[]>([]);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [filters, setFilters] = useState({
    status: undefined as string | undefined,
    source_status: undefined as string | undefined,
    priority: undefined as string | undefined,
    search: '',
  });
  const [createModalVisible, setCreateModalVisible] = useState(false);
  const [reviewAgent, setReviewAgent] = useState<string | null>(null);
  const [detailBugId, setDetailBugId] = useState<string | null>(null);
  const [detailDirty, setDetailDirty] = useState(false);
  const [form] = Form.useForm();
  const hasActiveTasks = bugs.some((bug) => ['analyzing', 'fixing', 'testing'].includes(bug.status));

  const loadSourceStatuses = async (silent = false) => {
    try {
      setSourceStatuses(await bugService.sourceStatuses());
    } catch (error) {
      if (!silent) message.error('加载云效状态筛选项失败');
      console.error(error);
    }
  };

  useEffect(() => {
    loadSourceStatuses();
  }, []);

  useEffect(() => {
    loadBugs();
  }, [page, pageSize, filters]);

  useEffect(() => {
    if (!hasActiveTasks) return;
    const interval = setInterval(loadBugs, 3000);
    return () => clearInterval(interval);
  }, [hasActiveTasks, page, pageSize, filters]);

  useEffect(() => {
    const interval = setInterval(() => {
      void loadBugs(true);
      void loadSourceStatuses(true);
    }, 30000);
    return () => clearInterval(interval);
  }, [page, pageSize, filters]);

  const loadBugs = async (silent = false) => {
    if (!silent) setLoading(true);
    try {
      const response = await bugService.list({
        page,
        page_size: pageSize,
        exclude_closed_yunxiao: true,
        ...filters,
      });
      setBugs(response.items || []);
      setTotal(response.total || 0);
    } catch (error) {
      if (!silent) message.error('加载 Bug 列表失败');
      console.error(error);
    } finally {
      if (!silent) setLoading(false);
    }
  };

  const handleSync = async () => {
    setSyncing(true);
    try {
      const result = await bugService.sync();
      message.success(
        `同步 ${result.total} 条：新增 ${result.created}，更新 ${result.updated}，无变化 ${result.unchanged}`,
      );
      await loadBugs();
      await loadSourceStatuses();
    } catch (error) {
      message.error(
        axios.isAxiosError(error)
          ? error.response?.data?.detail || '同步失败，请检查网络或后端服务'
          : '同步失败',
      );
      console.error(error);
    } finally {
      setSyncing(false);
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

  const handleSubmitTask = async (bugId: string) => {
    setSubmitting((ids) => [...ids, bugId]);
    try {
      await bugService.submitTask({ bug_id: bugId, priority: 5 });
      message.success('任务已加入修复队列，可在 Agent 状态中查看进度');
      await loadBugs();
    } catch (error) {
      message.error(
        axios.isAxiosError(error) ? error.response?.data?.detail || '提交任务失败' : '提交任务失败',
      );
      console.error(error);
    } finally {
      setSubmitting((ids) => ids.filter((id) => id !== bugId));
    }
  };

  const handleCreateBug = async (values: any) => {
    try {
      await bugService.create({
        bug_id: values.bug_id,
        title: values.title,
        description: values.description,
        priority: values.priority,
        source: 'manual',
        repo_path: values.repo_path,
        branch: values.branch || 'main',
        test_command: values.test_command,
      });
      message.success('Bug 创建成功');
      setCreateModalVisible(false);
      form.resetFields();
      loadBugs();
    } catch (error) {
      message.error('创建 Bug 失败');
      console.error(error);
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
      case 'awaiting_confirmation':
      case 'awaiting_plan_confirmation':
        return 'warning';
      case 'fixing':
      case 'analyzing':
        return 'processing';
      case 'failed':
        return 'error';
      default:
        return 'default';
    }
  };

  const columns = [
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
            changeDetails(bug.bug_id);
          }}
        />
      ),
    },
    {
      title: '悬赏等级',
      dataIndex: 'priority',
      key: 'priority',
      width: 130,
      render: (priority: string) => priorityRender(priority),
    },
    {
      title: '云效项目',
      dataIndex: 'source_project',
      key: 'source_project',
      width: 160,
      render: (project?: string) => (project ? <Link to="/projects">{project}</Link> : '-'),
    },
    {
      title: '云效状态',
      dataIndex: 'source_status',
      key: 'source_status',
      width: 140,
      render: (_: string | undefined, bug: Bug) => (
        <YunxiaoStatusCell
          bug={bug}
          onChanged={(status) => {
            if (HIDDEN_YUNXIAO.includes(status)) {
              void loadBugs();
            } else {
              setBugs((items) => items.map((item) => (item.bug_id === bug.bug_id ? { ...item, source_status: status } : item)));
            }
            void loadSourceStatuses();
          }}
        />
      ),
    },
    {
      title: '修复状态',
      dataIndex: 'status',
      key: 'status',
      width: 130,
      render: (status: Bug['status']) => <Tag color={statusColor(status)}>{getStatusLabel(status)}</Tag>,
    },
    {
      title: '修复任务',
      key: 'repair_task',
      width: 140,
      render: (_: unknown, record: Bug) =>
        record.assigned_agent_id ? (
          <Button type="link" size="small" onClick={() => setReviewAgent(record.assigned_agent_id!)}>
            {record.status === 'awaiting_plan_confirmation'
              ? '审查修复方案'
              : record.status === 'awaiting_confirmation'
                ? '确认修复结果'
                : '查看修复任务'}
          </Button>
        ) : (
          '-'
        ),
    },
    {
      title: '操作',
      key: 'action',
      width: 220,
      render: (_: any, record: Bug) => (
        <div className="bug-row-actions">
          <div>
            {record.source === 'yunxiao' &&
              record.source_url?.startsWith('https://devops.aliyun.com/') && (
                <Button
                  type="link"
                  size="small"
                  icon={<ExportOutlined />}
                  href={record.source_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  aria-label={`在新标签页查看云效原单：${record.title}`}
                >
                  云效原单
                </Button>
              )}
          </div>
          <Button
            type="link"
            size="small"
            icon={<PlayCircleOutlined />}
            onClick={() => handleSubmitTask(record.bug_id)}
            loading={submitting.includes(record.bug_id)}
            disabled={
              !['pending', 'failed'].includes(record.status) ||
              ['已修复', '已关闭', '暂不修复'].includes(record.source_status || '')
            }
          >
            修复
          </Button>
        </div>
      ),
    },
  ];

  return (
    <div>
      <div className="page-heading">
        <div>
          <div className="page-eyebrow">🎯 BUG BOUNTY BOARD</div>
          <h1>Bug 悬赏榜</h1>
          <p className="page-subtitle">
            服务运行期间会自动从云效同步缺陷，也可手动同步或录入悬赏。点击标题补充复现线索，一键派遣特工出击
          </p>
        </div>
      </div>

      <div className="filter-toolbar">
        <Search
          placeholder="搜索 Bug"
          aria-label="搜索 Bug"
          onSearch={(value) => {
            setPage(1);
            setFilters({ ...filters, search: value });
          }}
          allowClear
        />
        <Select
          placeholder="修复状态"
          aria-label="筛选修复状态"
          allowClear
          onChange={(value) => {
            setPage(1);
            setFilters({ ...filters, status: value });
          }}
        >
          {Object.entries(repairStatusLabels).map(([value, label]) => (
            <Option key={value} value={value}>
              {label}
            </Option>
          ))}
        </Select>
        <Select
          placeholder="云效状态"
          aria-label="筛选云效状态"
          allowClear
          options={sourceStatuses.map((status) => ({ value: status, label: status }))}
          onChange={(value) => {
            setPage(1);
            setFilters({ ...filters, source_status: value });
          }}
        />
        <Select
          placeholder="优先级"
          aria-label="筛选 Bug 优先级"
          allowClear
          onChange={(value) => {
            setPage(1);
            setFilters({ ...filters, priority: value });
          }}
        >
          <Option value="critical">关键</Option>
          <Option value="important">重要</Option>
          <Option value="normal">普通</Option>
        </Select>
        <div className="filter-toolbar-actions">
          <Button icon={<SyncOutlined />} onClick={handleSync} loading={syncing} title="后台会定时自动同步，点此立即同步">
            从云效同步
          </Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateModalVisible(true)}>
            创建 Bug
          </Button>
        </div>
      </div>
      <section className="data-section">
        <Table
          scroll={{ x: 1070 }}
          columns={columns}
          dataSource={bugs}
          rowKey="bug_id"
          loading={loading}
          pagination={{
            current: page,
            pageSize: pageSize,
            total: total,
            onChange: (p, ps) => {
              setPage(p);
              setPageSize(ps || 10);
            },
            showSizeChanger: true,
            showTotal: (t) => `共 ${t} 条`,
          }}
        />
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
              onStarted={() => void loadBugs()}
            />
          )}
        </Drawer>
      </section>
      <RepairReview
        agentId={reviewAgent}
        onClose={() => setReviewAgent(null)}
        onConfirmed={() => void loadBugs()}
      />

      <Modal
        title="创建 Bug"
        open={createModalVisible}
        onCancel={() => {
          setCreateModalVisible(false);
          form.resetFields();
        }}
        onOk={() => form.submit()}
      >
        <Form form={form} layout="vertical" onFinish={handleCreateBug}>
          <Form.Item
            name="bug_id"
            label="Bug ID"
            rules={[{ required: true, message: '请输入 Bug ID' }]}
          >
            <Input placeholder="BUG-001" />
          </Form.Item>
          <Form.Item
            name="title"
            label="标题"
            rules={[{ required: true, message: '请输入标题' }]}
          >
            <Input placeholder="Bug 标题" />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <Input.TextArea rows={4} placeholder="Bug 详细描述" />
          </Form.Item>
          <Form.Item
            name="priority"
            label="优先级"
            rules={[{ required: true, message: '请选择优先级' }]}
          >
            <Select>
              <Option value="critical">关键</Option>
              <Option value="important">重要</Option>
              <Option value="normal">普通</Option>
            </Select>
          </Form.Item>
          <Form.Item
            name="repo_path"
            label="仓库路径"
            rules={[{ required: true, message: '请输入仓库路径' }]}
          >
            <Input placeholder="/path/to/repo" />
          </Form.Item>
          <Form.Item name="branch" label="分支">
            <Input placeholder="main" />
          </Form.Item>
          <Form.Item
            name="test_command"
            label="验证命令"
            rules={[{ required: true, message: '请输入本地验证命令' }]}
          >
            <Input placeholder="pnpm install --frozen-lockfile && pnpm run build" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
};

export default BugList;
