import { useEffect, useRef, useState } from 'react';
import axios from 'axios';
import {
  AutoComplete,
  Button,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Select,
  Space,
  Table,
  Tag,
  message,
} from 'antd';
import { DownOutlined, EditOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons';
import {
  projectService,
  type Project,
  type ProjectConfig,
  type RepositoryBranches,
} from '../../services/projectService';
import ProjectDevelopment from './ProjectDevelopment';

function errorMessage(error: unknown, fallback: string) {
  const detail = axios.isAxiosError(error) ? error.response?.data?.detail : undefined;
  return typeof detail === 'string' ? detail : fallback;
}

function showError(error: unknown, fallback: string) {
  message.error(errorMessage(error, fallback));
}

export default function ProjectList() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(false);
  const [editing, setEditing] = useState<Project | null>(null);
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [form] = Form.useForm<ProjectConfig & { project_id: string }>();
  const repoPath = Form.useWatch('repo_path', form) as string | undefined;
  const [branchData, setBranchData] = useState<RepositoryBranches | null>(null);
  const [branchesLoading, setBranchesLoading] = useState(false);
  const [branchError, setBranchError] = useState('');
  const [branchSearch, setBranchSearch] = useState('');
  const [branchDropdownOpen, setBranchDropdownOpen] = useState(false);
  const branchRequest = useRef<{ path: string; controller: AbortController } | null>(null);

  const resetBranches = () => {
    branchRequest.current?.controller.abort();
    branchRequest.current = null;
    setBranchData(null);
    setBranchesLoading(false);
    setBranchError('');
    setBranchSearch('');
    setBranchDropdownOpen(false);
  };

  const loadBranches = async (repoPath: string, refresh = false) => {
    const path = repoPath.trim();
    if (!path || (!refresh && branchRequest.current?.path === path)) return;
    branchRequest.current?.controller.abort();
    const request = { path, controller: new AbortController() };
    branchRequest.current = request;
    setBranchData(null);
    setBranchError('');
    setBranchesLoading(true);
    try {
      const data = await projectService.branches(path, request.controller.signal);
      if (branchRequest.current === request) setBranchData(data);
    } catch (error) {
      if (branchRequest.current === request && !axios.isCancel(error)) {
        setBranchError(errorMessage(error, '读取本地分支失败，请点击刷新重试'));
      }
    } finally {
      if (branchRequest.current === request) setBranchesLoading(false);
    }
  };

  const close = () => {
    resetBranches();
    setOpen(false);
  };

  const matchingBranches =
    branchData?.branches.filter((branch) =>
      branch.toLowerCase().includes(branchSearch.toLowerCase()),
    ) || [];
  const branchStatus = branchesLoading
    ? '正在读取本地分支…'
    : branchError ||
      (!repoPath?.trim()
        ? '请先填写本地仓库路径'
        : !branchData
          ? '点击分支框或刷新按钮读取本地分支'
          : !branchData.branches.length
            ? '仓库暂无本地分支，请确认已创建首次提交'
            : !matchingBranches.length
              ? '没有匹配的本地分支；也可直接填写其他 Git 引用，保存时会验证'
              : '');

  const load = async () => {
    setLoading(true);
    try {
      setProjects(await projectService.list());
    } catch (error) {
      showError(error, '加载项目失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);
  useEffect(
    () => () => {
      branchRequest.current?.controller.abort();
      branchRequest.current = null;
    },
    [],
  );

  const edit = (project: Project | null) => {
    resetBranches();
    setEditing(project);
    form.resetFields();
    form.setFieldsValue(
      project
        ? {
            project_id: project.project_id,
            name: project.name,
            repo_path: project.repo_path || '',
            branch: project.branch || '',
            test_command: project.test_command || '',
            preview_command: project.preview_command || '',
            preview_env_files: project.preview_env_files || [],
            preview_port: project.preview_port,
          }
        : {
            project_id: '',
            name: '',
            repo_path: '',
            branch: '',
            test_command: '',
            preview_command: '',
            preview_env_files: [],
            preview_port: null,
          },
    );
    setOpen(true);
    if (project?.repo_path) void loadBranches(project.repo_path);
  };

  const save = async (values: ProjectConfig & { project_id: string }) => {
    setSaving(true);
    try {
      const { project_id, ...config } = values;
      if (!editing && projects.some((project) => project.project_id === project_id.trim())) {
        message.error('项目已存在，请在列表中配置仓库');
        return;
      }
      await projectService.save(project_id.trim(), config);
      message.success('项目配置已保存，后续任务将使用此配置');
      close();
      await load();
    } catch (error) {
      showError(error, '保存项目失败');
    } finally {
      setSaving(false);
    }
  };

  const unbind = async (project: Project) => {
    try {
      await projectService.unbind(project.project_id);
      message.success('仓库绑定已解除，Bug 已保留');
      await load();
    } catch (error) {
      showError(error, '解除绑定失败');
    }
  };

  return (
    <div>
      <div className="page-heading">
        <div>
          <div className="page-eyebrow">🗂️ REPOSITORY BINDINGS</div>
          <h1>项目与代码仓库绑定</h1>
          <p className="page-subtitle">
            配置本地 Git 仓库、验证命令与预览入口端口，让 Agent 在独立工作树中安全动刀与拉起预览
          </p>
        </div>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => edit(null)}>
          添加项目
        </Button>
      </div>

      <div className="page-toolbar">
        <p>
          将云效项目绑定到本地仓库，所属 Bug 自动使用该项目的修复配置。修复在独立工作树中进行，保留修改供审查。请配置验证命令，只有验证通过才会标记为已解决。
        </p>
      </div>

      <section className="data-section">
        <Table<Project>
          rowKey="project_id"
          loading={loading}
          dataSource={projects}
          scroll={{ x: 1500 }}
          locale={{ emptyText: '暂无项目。请先从云效同步 Bug，或手动添加项目。' }}
          columns={[
            {
              title: '云效项目',
              dataIndex: 'name',
              render: (name: string, project: Project) => (
                <div>
                  <strong>{name}</strong>
                  <div style={{ color: 'var(--muted)', fontSize: 12 }}>{project.project_id}</div>
                </div>
              ),
            },
            { title: 'Bug 数量', dataIndex: 'bug_count', width: 100 },
            {
              title: '本地仓库',
              dataIndex: 'repo_path',
              render: (path?: string) => path || <Tag>未绑定</Tag>,
            },
            {
              title: '修复分支',
              dataIndex: 'branch',
              render: (branch?: string) => (branch ? <Tag color="processing">{branch}</Tag> : '—'),
            },
            {
              title: '验证命令',
              dataIndex: 'test_command',
              render: (command?: string) => command || '未配置',
            },
            {
              title: '预览命令',
              dataIndex: 'preview_command',
              render: (command?: string) => command || '未配置',
            },
            {
              title: '开发入口',
              width: 420,
              render: (_: unknown, project: Project) =>
                project.repo_path && project.preview_port ? (
                  <ProjectDevelopment
                    projectId={project.project_id}
                    port={project.preview_port}
                  />
                ) : (
                  '配置固定入口端口后启用'
                ),
            },
            {
              title: '操作',
              width: 240,
              render: (_: unknown, project: Project) => (
                <Space>
                  <Button size="small" icon={<EditOutlined />} onClick={() => edit(project)}>
                    配置仓库
                  </Button>
                  {project.repo_path && (
                    <Popconfirm
                      title="解除仓库绑定？"
                      description="Bug 保留，后续任务将不再使用此项目配置。"
                      onConfirm={() => unbind(project)}
                      okText="解除绑定"
                      cancelText="取消"
                    >
                      <Button size="small" danger>
                        解除绑定
                      </Button>
                    </Popconfirm>
                  )}
                </Space>
              ),
            },
          ]}
        />
      </section>
      <Modal
        title={editing ? `配置仓库 · ${editing.name}` : '添加项目'}
        open={open}
        onCancel={() => !saving && close()}
        onOk={() => form.submit()}
        confirmLoading={saving}
        okText="保存配置"
        cancelText="取消"
        destroyOnClose
      >
        <Form form={form} layout="vertical" onFinish={save}>
          <Form.Item
            name="project_id"
            label="云效项目 ID"
            rules={[{ required: true, whitespace: true, message: '请输入云效项目 ID' }]}
            extra="云效项目地址中 /project/ 后的项目 ID。"
          >
            <Input disabled={!!editing} />
          </Form.Item>
          <Form.Item name="name" label="项目名称" rules={[{ required: true, whitespace: true }]}>
            <Input />
          </Form.Item>
          <Form.Item
            name="repo_path"
            label="本地仓库路径"
            rules={[{ required: true, whitespace: true }]}
            extra="填写后端所在机器上的 Git 仓库根目录绝对路径。"
          >
            <Input
              placeholder="/Users/your-name/Code/project"
              onChange={() => {
                resetBranches();
                form.setFieldValue('branch', '');
              }}
              onBlur={(event) => {
                void loadBranches(event.target.value);
              }}
            />
          </Form.Item>
          <Form.Item
            label="修复基于的分支"
            htmlFor="project-branch"
            required
            extra={
              <>
                <div>读取后端所在机器上的本地分支，也可直接填写 Git 引用。</div>
                {branchStatus && (
                  <div
                    role={branchError ? 'alert' : 'status'}
                    style={branchError ? { color: '#ff4d4f' } : undefined}
                  >
                    {branchStatus}
                  </div>
                )}
              </>
            }
          >
            <Space.Compact style={{ width: '100%' }}>
              <Form.Item
                name="branch"
                noStyle
                rules={[{ required: true, whitespace: true, message: '请选择分支或填写 Git 引用' }]}
              >
                <AutoComplete<string>
                  style={{ flex: 1, minWidth: 0 }}
                  placeholder="搜索本地分支或填写 Git 引用"
                  allowClear
                  open={branchDropdownOpen}
                  filterOption={false}
                  options={
                    matchingBranches.length
                      ? matchingBranches.map((branch) => ({
                          value: branch,
                          label: (
                            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                              <span>{branch}</span>
                              {branch === branchData?.current_branch && <Tag>当前分支</Tag>}
                            </div>
                          ),
                        }))
                      : [{ value: '', disabled: true, label: branchStatus }]
                  }
                  onSearch={setBranchSearch}
                  onSelect={() => setBranchSearch('')}
                  onFocus={() => {
                    setBranchDropdownOpen(true);
                    void loadBranches(form.getFieldValue('repo_path') || '');
                  }}
                  onOpenChange={(visible) => {
                    setBranchDropdownOpen(visible);
                    if (!visible) setBranchSearch('');
                  }}
                >
                  <Input id="project-branch" suffix={<DownOutlined aria-hidden />} />
                </AutoComplete>
              </Form.Item>
              <Button
                icon={<ReloadOutlined />}
                loading={branchesLoading}
                disabled={!repoPath?.trim()}
                title="刷新本地分支"
                aria-label="刷新本地分支"
                onClick={() => {
                  void loadBranches(form.getFieldValue('repo_path') || '', true);
                }}
              />
            </Space.Compact>
          </Form.Item>
          <Form.Item
            name="test_command"
            label="验证命令"
            extra="在仓库根目录运行，例如 pnpm test。此处仅保存配置，不会执行命令。"
          >
            <Input placeholder="填写项目实际使用的测试或构建命令" />
          </Form.Item>
          <Form.Item
            name="preview_command"
            label="开发与验收启动命令"
            extra="原项目和修复工作树均使用此命令。保留 {port}，由系统分配内部端口；浏览器访问下面配置的固定入口。依赖需提前安装，或在命令前加安装步骤。"
          >
            <Input placeholder="pnpm dev:test --host 127.0.0.1 --port {port} --strictPort" />
          </Form.Item>
          <Form.Item
            name="preview_port"
            label="固定入口端口"
            extra="填写原浏览器使用的端口，例如 3000。首次接入先停止占用该端口的原服务，再通过项目列表启动项目；之后预览只切换内容，原窗口刷新即可，地址和参数保持不变。留空则使用独立端口预览。"
          >
            <InputNumber
              min={1}
              max={65535}
              precision={0}
              placeholder="例如 3000"
              style={{ width: '100%' }}
            />
          </Form.Item>
          <Form.Item
            name="preview_env_files"
            label="预览环境文件"
            extra="按需填写相对路径，例如 .env.local、frontend/.env.local。从本地仓库复制到工作树，仅复制被 Git 忽略的文件，已有文件不覆盖。"
          >
            <Select mode="tags" placeholder="输入路径后按回车，留空则不复制" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
