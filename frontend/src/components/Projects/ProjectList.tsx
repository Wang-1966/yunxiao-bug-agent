import { useEffect, useState } from 'react';
import axios from 'axios';
import { Alert, Button, Form, Input, Modal, Popconfirm, Space, Table, Tag, message } from 'antd';
import { EditOutlined, PlusOutlined } from '@ant-design/icons';
import { projectService, type Project, type ProjectConfig } from '../../services/projectService';

function showError(error: unknown, fallback: string) {
  const detail = axios.isAxiosError(error) ? error.response?.data?.detail : undefined;
  message.error(typeof detail === 'string' ? detail : fallback);
}

export default function ProjectList() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(false);
  const [editing, setEditing] = useState<Project | null>(null);
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [form] = Form.useForm<ProjectConfig & { project_id: string }>();

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

  useEffect(() => { void load(); }, []);

  const edit = (project: Project | null) => {
    setEditing(project);
    form.resetFields();
    form.setFieldsValue(project ? {
      project_id: project.project_id, name: project.name, repo_path: project.repo_path || '',
      branch: project.branch || '', test_command: project.test_command || '',
    } : { project_id: '', name: '', repo_path: '', branch: '', test_command: '' });
    setOpen(true);
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
      setOpen(false);
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

  return <div>
    <div className="page-heading">
      <div><div className="page-eyebrow">PROJECTS</div><h1>项目管理</h1></div>
      <Button type="primary" icon={<PlusOutlined />} onClick={() => edit(null)}>添加项目</Button>
    </div>
    <Alert type="info" showIcon message="将云效项目绑定到本地仓库，所属 Bug 自动使用该项目的修复配置。" description="修复在独立工作树中进行，保留修改供审查。请配置验证命令，只有实际验证通过才会标记为已解决。" style={{ marginBottom: 24 }} />
    <section className="data-section">
      <Table<Project> rowKey="project_id" loading={loading} dataSource={projects} scroll={{ x: 1100 }}
        locale={{ emptyText: '暂无项目。请先从云效同步 Bug，或手动添加项目。' }}
        columns={[
          { title: '云效项目', dataIndex: 'name', render: (name: string, project: Project) => <div><strong>{name}</strong><div style={{ color: '#69756e', fontSize: 12 }}>{project.project_id}</div></div> },
          { title: 'Bug 数量', dataIndex: 'bug_count', width: 100 },
          { title: '本地仓库', dataIndex: 'repo_path', render: (path?: string) => path || <Tag>未绑定</Tag> },
          { title: '修复分支', dataIndex: 'branch', render: (branch?: string) => branch || '—' },
          { title: '验证命令', dataIndex: 'test_command', render: (command?: string) => command || '未配置' },
          { title: '操作', width: 240, render: (_: unknown, project: Project) => <Space>
            <Button size="small" icon={<EditOutlined />} onClick={() => edit(project)}>配置仓库</Button>
            {project.repo_path && <Popconfirm title="解除仓库绑定？" description="Bug 保留，后续任务将不再使用此项目配置。" onConfirm={() => unbind(project)} okText="解除绑定" cancelText="取消">
              <Button size="small" danger>解除绑定</Button>
            </Popconfirm>}
          </Space> },
        ]} />
    </section>
    <Modal title={editing ? `配置仓库 · ${editing.name}` : '添加项目'} open={open} onCancel={() => !saving && setOpen(false)}
      onOk={() => form.submit()} confirmLoading={saving} okText="保存配置" cancelText="取消" destroyOnClose>
      <Form form={form} layout="vertical" onFinish={save}>
        <Form.Item name="project_id" label="云效项目 ID" rules={[{ required: true, whitespace: true, message: '请输入云效项目 ID' }]} extra="云效项目地址中 /project/ 后的项目 ID。">
          <Input disabled={!!editing} />
        </Form.Item>
        <Form.Item name="name" label="项目名称" rules={[{ required: true, whitespace: true }]}><Input /></Form.Item>
        <Form.Item name="repo_path" label="本地仓库路径" rules={[{ required: true, whitespace: true }]} extra="填写后端所在机器上的 Git 仓库根目录绝对路径。"><Input placeholder="/Users/your-name/Code/project" /></Form.Item>
        <Form.Item name="branch" label="修复基于的分支" rules={[{ required: true, whitespace: true }]} extra="必须是仓库中存在的分支或 Git 引用。"><Input placeholder="例如 main 或 origin/main" /></Form.Item>
        <Form.Item name="test_command" label="验证命令" extra="在仓库根目录运行，例如 pnpm test。此处仅保存配置，不会执行命令。"><Input placeholder="填写项目实际使用的测试或构建命令" /></Form.Item>
      </Form>
    </Modal>
  </div>;
}
