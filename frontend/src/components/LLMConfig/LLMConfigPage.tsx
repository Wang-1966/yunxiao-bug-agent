import React, { useEffect, useState } from 'react';
import {
  Form,
  Input,
  Switch,
  Button,
  Space,
  Select,
  InputNumber,
  message,
  Table,
  Tag,
  Tabs,
  Modal,
  Popconfirm,
  AutoComplete,
  Tooltip,
  Typography,
} from 'antd';
import {
  CheckCircleOutlined,
  DeleteOutlined,
  DownloadOutlined,
  EditOutlined,
  PlusOutlined,
  SaveOutlined,
} from '@ant-design/icons';
import { llmConfigService } from '../../services/llmConfigService';
import type { LLMConfig, LLMProvider, YunxiaoConfig } from '../../types';

const { Option } = Select;

const STAGE_META: Record<string, { badge: string; title: string; tip: string }> = {
  analyze: {
    badge: '🔎 第一棒 · 根因侦察',
    title: 'ANALYZE 阶段',
    tip: '建议使用高推理能力模型深入调用链定位根因',
  },
  propose: {
    badge: '💡 第二棒 · 方案设计',
    title: 'PROPOSE 阶段',
    tip: '权衡改动影响面，生成多套候选方案供你拍板',
  },
  fix: {
    badge: '🛠️ 第三棒 · 手术改码',
    title: 'FIX 阶段',
    tip: '按照确认方案在独立工作树编写高性价比补丁',
  },
  verify: {
    badge: '🧪 第四棒 · 试炼验证',
    title: 'VERIFY 阶段',
    tip: '结合测试与构建输出校验修复完整性',
  },
};

function errorDetail(error: unknown, fallback: string) {
  const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  return typeof detail === 'string' ? detail : fallback;
}

function createProviderId(name: string, existing: string[]) {
  const slug = name
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
  const base = slug || 'provider';
  let id = base;
  let index = 2;
  while (existing.includes(id)) {
    id = `${base}-${index}`;
    index += 1;
  }
  return id;
}

const LLMConfigPage: React.FC = () => {
  const [config, setConfig] = useState<LLMConfig | null>(null);
  const [loading, setLoading] = useState(false);
  const [testingProvider, setTestingProvider] = useState<string | null>(null);
  const [yunxiaoConfig, setYunxiaoConfig] = useState<YunxiaoConfig | null>(null);
  const [yunxiaoLoading, setYunxiaoLoading] = useState(false);
  const [form] = Form.useForm();
  const stageConfig = Form.useWatch('stage_config', form);
  const [yunxiaoForm] = Form.useForm();
  const [promptForm] = Form.useForm();
  const [providerForm] = Form.useForm();
  const [editorOpen, setEditorOpen] = useState(false);
  const [editingProvider, setEditingProvider] = useState<LLMProvider | null>(null);
  const [savingProvider, setSavingProvider] = useState(false);
  const [fetchingModels, setFetchingModels] = useState(false);
  const [modelOptions, setModelOptions] = useState<string[]>([]);
  const [modelWindows, setModelWindows] = useState<Record<string, number>>({});

  const getProviderModelOptions = (providerId: string) => {
    const provider = config?.providers.find((item) => item.id === providerId);
    return [...new Set([provider?.default_model, ...(provider?.available_models || [])])]
      .filter((model): model is string => !!model)
      .map((value) => ({ value }));
  };

  const handleProviderChange = (providerId: string, stage: string) => {
    const model = getProviderModelOptions(providerId)[0]?.value || '';
    form.setFieldValue(['stage_config', stage, 'model'], model);
  };

  useEffect(() => {
    loadConfig();
    llmConfigService
      .getYunxiao()
      .then((yunxiao) => {
        setYunxiaoConfig(yunxiao);
        yunxiaoForm.setFieldsValue(yunxiao);
      })
      .catch(() => message.error('加载云效配置失败'));
  }, []);

  const loadConfig = async () => {
    setLoading(true);
    try {
      const data = await llmConfigService.get();
      setConfig(data);
      form.setFieldsValue(data);
      promptForm.setFieldsValue({ response_style: data.response_style || 'detailed' });
    } catch (error) {
      message.error('加载配置失败');
      console.error(error);
    } finally {
      setLoading(false);
    }
  };

  const handleSaveYunxiao = async (values: {
    api_base: string;
    org_id: string;
    project_id: string;
    access_token?: string;
  }) => {
    setYunxiaoLoading(true);
    try {
      const updated = await llmConfigService.updateYunxiao(values);
      setYunxiaoConfig(updated);
      yunxiaoForm.resetFields(['access_token']);
      message.success('云效配置保存成功');
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '保存云效配置失败');
    } finally {
      setYunxiaoLoading(false);
    }
  };

  const handleSave = async (values: any) => {
    const {
      providers: _providers,
      default_provider: _defaultProvider,
      default_model: _defaultModel,
      use_stage_config: _useStageConfig,
      ...rest
    } = values;
    const payload = Object.prototype.hasOwnProperty.call(values, 'stage_config')
      ? { ...rest, use_stage_config: true }
      : rest;
    setLoading(true);
    try {
      const updated = await llmConfigService.update(payload);
      setConfig(updated);
      message.success('配置保存成功');
    } catch (error) {
      message.error(errorDetail(error, '保存配置失败'));
    } finally {
      setLoading(false);
    }
  };

  const persistProviders = async (
    providers: LLMProvider[],
    defaults?: { default_provider: string; default_model: string },
  ) => {
    const updated = await llmConfigService.update({ providers, ...defaults });
    setConfig(updated);
    return updated;
  };

  const fetchProviderModels = async () => {
    const baseUrl = String(providerForm.getFieldValue('base_url') || '').trim();
    if (!baseUrl) {
      message.error('请先填写 Base URL');
      return;
    }
    setFetchingModels(true);
    try {
      const { models, context_windows = {} } = await llmConfigService.fetchModels({
        provider_id: editingProvider?.id,
        base_url: baseUrl,
        api_key: providerForm.getFieldValue('api_key'),
      });
      const current = providerForm.getFieldValue('default_model')?.trim();
      setModelWindows((existing) => ({ ...context_windows, ...existing }));
      setModelOptions(models);
      providerForm.setFieldsValue({
        available_models: models,
        default_model: current && models.includes(current) ? current : models[0],
      });
      message.success(`已获取 ${models.length} 个模型`);
    } catch (error) {
      message.error(errorDetail(error, '获取模型列表失败'));
    } finally {
      setFetchingModels(false);
    }
  };

  const openProviderEditor = (provider: LLMProvider | null) => {
    setEditingProvider(provider);
    setModelOptions(provider?.available_models || []);
    setModelWindows(provider?.context_windows || {});
    providerForm.resetFields();
    providerForm.setFieldsValue(
      provider
        ? {
            name: provider.name,
            base_url: provider.base_url,
            default_model: provider.default_model,
            available_models: provider.available_models,
            enabled: provider.enabled,
            stream_usage: provider.stream_usage ?? false,
          }
        : { enabled: true },
    );
    setEditorOpen(true);
  };

  const saveProvider = async (values: {
    name: string;
    base_url: string;
    api_key?: string;
    default_model: string;
    available_models?: string[];
    enabled?: boolean;
    stream_usage?: boolean;
  }) => {
    if (!config) return;
    const apiKey = values.api_key?.trim() || '';
    if (!editingProvider && !apiKey) {
      message.error('请输入 API Key');
      return;
    }
    const defaultModel = values.default_model.trim();
    const models = [
      ...new Set([
        ...(values.available_models || []).map((model) => model.trim()).filter(Boolean),
        defaultModel,
      ]),
    ];
    const provider: LLMProvider = {
      id:
        editingProvider?.id ||
        createProviderId(
          values.name,
          config.providers.map((item) => item.id),
        ),
      name: values.name.trim(),
      type: 'openai-compatible',
      base_url: values.base_url.trim(),
      api_key: apiKey,
      available_models: models,
      default_model: defaultModel,
      enabled: values.enabled ?? true,
      context_windows: modelWindows,
      stream_usage: values.stream_usage ?? false,
    };
    const providers = editingProvider
      ? config.providers.map((item) => (item.id === editingProvider.id ? provider : item))
      : [...config.providers, provider];
    setSavingProvider(true);
    try {
      const defaults =
        !config.default_provider || config.default_provider === provider.id
          ? {
              default_provider: provider.id,
              default_model: provider.default_model,
            }
          : undefined;
      await persistProviders(providers, defaults);
      message.success(editingProvider ? '提供商已更新' : '提供商已添加');
      setEditorOpen(false);
    } catch (error) {
      message.error(errorDetail(error, '保存提供商失败'));
    } finally {
      setSavingProvider(false);
    }
  };

  const removeProvider = async (provider: LLMProvider) => {
    if (!config) return;
    const usedStage = Object.entries(config.stage_config || {}).find(
      ([, stage]) => stage.provider === provider.id,
    );
    if (usedStage) {
      message.error(`该提供商仍被 ${usedStage[0]} 阶段使用，请先修改阶段配置`);
      return;
    }
    const providers = config.providers.filter((item) => item.id !== provider.id);
    const defaults =
      config.default_provider === provider.id
        ? {
            default_provider: providers[0]?.id || '',
            default_model: providers[0]?.default_model || '',
          }
        : undefined;
    setSavingProvider(true);
    try {
      await persistProviders(providers, defaults);
      message.success('提供商已删除');
    } catch (error) {
      message.error(errorDetail(error, '删除提供商失败'));
    } finally {
      setSavingProvider(false);
    }
  };

  const handleTestConnection = async (provider: LLMProvider) => {
    setTestingProvider(provider.id);
    try {
      const result = await llmConfigService.testConnection(provider);
      if (result.success) {
        message.success(`连接成功 (${result.latency_ms?.toFixed(0)}ms)`);
      } else {
        message.error(`连接失败: ${result.message}`);
      }
    } catch (error) {
      message.error('测试连接失败');
      console.error(error);
    } finally {
      setTestingProvider(null);
    }
  };

  const providerColumns = [
    {
      title: '提供商',
      dataIndex: 'name',
      key: 'name',
      render: (name: string) => <strong>{name}</strong>,
    },
    {
      title: 'Base URL',
      dataIndex: 'base_url',
      key: 'base_url',
      ellipsis: true,
    },
    {
      title: '默认模型',
      dataIndex: 'default_model',
      key: 'default_model',
      render: (model: string) => <Tag color="processing">{model}</Tag>,
    },
    {
      title: '状态',
      dataIndex: 'enabled',
      key: 'enabled',
      render: (enabled: boolean) => (
        <Tag color={enabled ? 'success' : 'default'}>{enabled ? '启用' : '禁用'}</Tag>
      ),
    },
    {
      title: '操作',
      key: 'action',
      render: (_: any, record: LLMProvider) => (
        <Space size={4} wrap>
          <Button size="small" icon={<EditOutlined />} onClick={() => openProviderEditor(record)}>
            编辑
          </Button>
          <Button
            size="small"
            icon={<CheckCircleOutlined />}
            loading={testingProvider === record.id}
            onClick={() => handleTestConnection(record)}
          >
            测试连接
          </Button>
          <Popconfirm
            title="删除该提供商？"
            description="删除后需要重新添加才能使用。"
            onConfirm={() => removeProvider(record)}
            okText="删除"
            cancelText="取消"
            okButtonProps={{ danger: true }}
          >
            <Button size="small" danger icon={<DeleteOutlined />}>
              删除
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <div className="settings-page">
      <div className="page-heading">
        <div>
          <div className="page-eyebrow">⚙️ ENGINE & RELAY SETTINGS</div>
          <h1>模型接力与通用配置</h1>
          <p className="page-subtitle">
            管理多模型提供商、配置四阶段接力策略、回答详略度与阿里云效连接参数
          </p>
        </div>
      </div>

      <Tabs
        defaultActiveKey="llm"
        renderTabBar={(props, DefaultTabBar) => (
          <div className="settings-sticky-header">
            <DefaultTabBar {...props} />
          </div>
        )}
        items={[
          {
            key: 'llm',
            label: 'LLM 配置',
            children: (
              <>
                <section className="data-section">
                  <div className="section-heading">
                    <h2>提供商列表</h2>
                    <Space>
                      <span>{config?.providers.length || 0} 个提供商</span>
                      <Button
                        type="primary"
                        icon={<PlusOutlined />}
                        onClick={() => openProviderEditor(null)}
                      >
                        添加提供商
                      </Button>
                    </Space>
                  </div>
                  <Table
                    scroll={{ x: 860 }}
                    locale={{ emptyText: '还没有提供商，点击右上角添加' }}
                    columns={providerColumns}
                    dataSource={config?.providers || []}
                    rowKey="id"
                    pagination={false}
                    loading={loading}
                  />
                </section>

                <Form
                  form={form}
                  layout="vertical"
                  onFinish={handleSave}
                  initialValues={config || {}}
                >
                  <section className="config-section">
                    <h2>阶段配置（四棒接力）</h2>
                    <div className="stage-grid">
                      {['analyze', 'propose', 'fix', 'verify'].map((stage) => {
                        const meta = STAGE_META[stage];
                        return (
                          <div key={stage} className="stage-fields">
                            <div className="page-eyebrow" style={{ marginBottom: 6 }}>
                              {meta.badge}
                            </div>
                            <h3>{meta.title}</h3>
                            <p
                              style={{
                                fontSize: 12,
                                color: 'var(--muted)',
                                marginBottom: 14,
                              }}
                            >
                              {meta.tip}
                            </p>
                            <Form.Item name={['stage_config', stage, 'provider']} label="提供商">
                              <Select
                                placeholder="选择提供商"
                                onChange={(providerId) => handleProviderChange(providerId, stage)}
                              >
                                {config?.providers.map((p) => (
                                  <Option key={p.id} value={p.id}>
                                    {p.name}
                                  </Option>
                                ))}
                              </Select>
                            </Form.Item>

                            <Form.Item name={['stage_config', stage, 'model']} label="模型">
                              <AutoComplete
                                placeholder="选择或输入模型名称"
                                options={getProviderModelOptions(stageConfig?.[stage]?.provider)}
                                filterOption={(input, option) =>
                                  String(option?.value).toLowerCase().includes(input.toLowerCase())
                                }
                              />
                            </Form.Item>

                            <Space>
                              <Form.Item
                                name={['stage_config', stage, 'temperature']}
                                label="Temperature"
                              >
                                <InputNumber min={0} max={2} step={0.1} style={{ width: 120 }} />
                              </Form.Item>

                              <Form.Item
                                name={['stage_config', stage, 'max_tokens']}
                                label="Max Tokens"
                              >
                                <InputNumber
                                  min={1000}
                                  max={32000}
                                  step={1000}
                                  style={{ width: 120 }}
                                />
                              </Form.Item>
                            </Space>
                          </div>
                        );
                      })}
                    </div>
                  </section>

                  <Form.Item>
                    <Button
                      type="primary"
                      htmlType="submit"
                      loading={loading}
                      icon={<SaveOutlined />}
                    >
                      保存配置
                    </Button>
                  </Form.Item>
                </Form>
                <Modal
                  title={editingProvider ? '编辑提供商' : '添加提供商'}
                  open={editorOpen}
                  onCancel={() => !savingProvider && setEditorOpen(false)}
                  onOk={() => providerForm.submit()}
                  confirmLoading={savingProvider}
                  okText="保存"
                  cancelText="取消"
                  destroyOnClose
                >
                  <Form form={providerForm} layout="vertical" onFinish={saveProvider}>
                    <Form.Item
                      name="name"
                      label="名称"
                      rules={[{ required: true, whitespace: true, message: '请输入名称' }]}
                    >
                      <Input placeholder="例如 Claude 中转" />
                    </Form.Item>
                    <Form.Item
                      name="base_url"
                      label="Base URL"
                      rules={[
                        { required: true, whitespace: true, message: '请输入 Base URL' },
                        {
                          pattern: /^https?:\/\/\S+$/,
                          message: '请输入以 http:// 或 https:// 开头的地址',
                        },
                      ]}
                    >
                      <Input placeholder="https://api.example.com/v1" />
                    </Form.Item>
                    <Form.Item
                      name="api_key"
                      label="API Key"
                      extra={editingProvider ? '留空则保持当前 Key 不变' : undefined}
                      rules={[
                        {
                          required: !editingProvider,
                          whitespace: true,
                          message: '请输入 API Key',
                        },
                      ]}
                    >
                      <Input.Password
                        placeholder={editingProvider ? '已配置，留空保持不变' : '请输入 API Key'}
                        autoComplete="new-password"
                      />
                    </Form.Item>
                    <Form.Item
                      label="默认模型"
                      required
                      extra="点击右侧按钮从提供商获取模型列表，也可以手动输入。"
                    >
                      <Space.Compact style={{ width: '100%' }}>
                        <Form.Item
                          name="default_model"
                          noStyle
                          rules={[{ required: true, whitespace: true, message: '请输入默认模型' }]}
                        >
                          <AutoComplete
                            style={{ width: '100%' }}
                            placeholder="例如 gpt-4.1"
                            options={modelOptions.map((model) => ({ value: model }))}
                            filterOption={(input, option) =>
                              String(option?.value).toLowerCase().includes(input.toLowerCase())
                            }
                          />
                        </Form.Item>
                        <Tooltip title="获取模型列表">
                          <Button
                            aria-label="获取模型列表"
                            icon={<DownloadOutlined />}
                            loading={fetchingModels}
                            onClick={fetchProviderModels}
                          />
                        </Tooltip>
                      </Space.Compact>
                    </Form.Item>
                    <Form.Item
                      name="available_models"
                      label="可用模型"
                      extra="获取模型列表后会自动填入，也可输入模型名后回车。默认模型会自动加入。"
                    >
                      <Select
                        mode="tags"
                        placeholder="可添加多个模型"
                        tokenSeparators={[',']}
                        options={modelOptions.map((model) => ({ value: model }))}
                      />
                    </Form.Item>
                    <Form.Item label="模型上下文窗口">
                      <Typography.Text type="secondary">按所选模型自动应用内置配置，无需填写；提供商返回窗口信息时优先采用。</Typography.Text>
                    </Form.Item>
                    <Form.Item name="stream_usage" label="请求流式 token 用量" valuePropName="checked"
                      extra="仅在提供商支持 stream_options.include_usage 时开启；用于比较估算和真实消耗。">
                      <Switch />
                    </Form.Item>
                    <Form.Item name="enabled" label="启用" valuePropName="checked">
                      <Switch />
                    </Form.Item>
                  </Form>
                </Modal>
              </>
            ),
          },
          {
            key: 'prompts',
            label: '提示词工程',
            children: (
              <Form
                form={promptForm}
                layout="vertical"
                onFinish={handleSave}
                disabled={!config || loading}
                initialValues={{ response_style: 'detailed' }}
              >
                <section className="config-section config-fields">
                  <h2>Agent 回答风格</h2>
                  <Form.Item
                    name="response_style"
                    label="回答详细程度"
                    rules={[{ required: true }]}
                    extra="控制根因分析、修复方案和修复说明的表达。各档位均保留必要依据、风险和验证要求，已生成的回答保持原样。"
                  >
                    <Select
                      options={[
                        { value: 'detailed', label: '具体 · 当前详细程度' },
                        { value: 'concise', label: '较简 · 保留关键原因和步骤' },
                        { value: 'minimal', label: '极简 · 只保留核心结论和方向' },
                      ]}
                    />
                  </Form.Item>
                  <Button
                    type="primary"
                    htmlType="submit"
                    loading={loading}
                    icon={<SaveOutlined />}
                  >
                    保存回答设置
                  </Button>
                </section>
              </Form>
            ),
          },
          {
            key: 'yunxiao',
            label: '云效 MCP 配置',
            children: (
              <Form
                form={yunxiaoForm}
                layout="vertical"
                onFinish={handleSaveYunxiao}
                disabled={!yunxiaoConfig || yunxiaoLoading}
              >
                <section className="config-section config-fields">
                  <h2>云效连接</h2>
                  <Form.Item name="api_base" label="API 地址" rules={[{ required: true }]}>
                    <Select
                      options={[{ value: 'https://openapi-rdc.aliyuncs.com', label: '云效 OpenAPI' }]}
                    />
                  </Form.Item>
                  <Form.Item
                    name="org_id"
                    label="组织 ID"
                    rules={[{ required: true, message: '请输入组织 ID' }]}
                  >
                    <Input placeholder="实际组织 ID" />
                  </Form.Item>
                  <Form.Item
                    name="project_id"
                    label="项目 ID"
                    rules={[{ required: true, message: '请输入项目 ID' }]}
                  >
                    <Input placeholder="项目 ID，填写 * 表示全部项目" />
                  </Form.Item>
                  <Form.Item
                    name="access_token"
                    label={
                      yunxiaoConfig?.token_configured
                        ? 'Access Token（留空保持不变）'
                        : 'Access Token'
                    }
                    rules={[
                      {
                        required: !yunxiaoConfig?.token_configured,
                        message: '请输入 Access Token',
                      },
                    ]}
                  >
                    <Input.Password
                      placeholder={
                        yunxiaoConfig?.token_configured ? '已配置，留空保持不变' : '请输入云效 Token'
                      }
                    />
                  </Form.Item>
                  <Button
                    type="primary"
                    htmlType="submit"
                    loading={yunxiaoLoading}
                    icon={<SaveOutlined />}
                  >
                    保存云效配置
                  </Button>
                </section>
              </Form>
            ),
          },
        ]}
      />
    </div>
  );
};

export default LLMConfigPage;
