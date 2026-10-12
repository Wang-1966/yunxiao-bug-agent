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
} from 'antd';
import { CheckCircleOutlined, SaveOutlined } from '@ant-design/icons';
import { llmConfigService } from '../../services/llmConfigService';
import type { LLMConfig, LLMProvider, YunxiaoConfig } from '../../types';

const { Option } = Select;

const LLMConfigPage: React.FC = () => {
  const [config, setConfig] = useState<LLMConfig | null>(null);
  const [loading, setLoading] = useState(false);
  const [testingProvider, setTestingProvider] = useState<string | null>(null);
  const [yunxiaoConfig, setYunxiaoConfig] = useState<YunxiaoConfig | null>(null);
  const [yunxiaoLoading, setYunxiaoLoading] = useState(false);
  const [form] = Form.useForm();
  const [yunxiaoForm] = Form.useForm();
  const [promptForm] = Form.useForm();

  useEffect(() => {
    loadConfig();
    llmConfigService.getYunxiao().then((yunxiao) => {
      setYunxiaoConfig(yunxiao);
      yunxiaoForm.setFieldsValue(yunxiao);
    }).catch(() => message.error('加载云效配置失败'));
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

  const handleSaveYunxiao = async (values: { api_base: string; org_id: string; project_id: string; access_token?: string }) => {
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
    setLoading(true);
    try {
      const updated = await llmConfigService.update(values);
      setConfig(updated);
      message.success('配置保存成功');
    } catch (error) {
      message.error('保存配置失败');
      console.error(error);
    } finally {
      setLoading(false);
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
    },
    {
      title: '状态',
      dataIndex: 'enabled',
      key: 'enabled',
      render: (enabled: boolean) => (
        <Tag color={enabled ? 'success' : 'default'}>
          {enabled ? '启用' : '禁用'}
        </Tag>
      ),
    },
    {
      title: '操作',
      key: 'action',
      render: (_: any, record: LLMProvider) => (
        <Button
          size="small"
          icon={<CheckCircleOutlined />}
          loading={testingProvider === record.id}
          onClick={() => handleTestConnection(record)}
        >
          测试连接
        </Button>
      ),
    },
  ];

  return (
    <div className="settings-page">
      <Tabs
        defaultActiveKey="llm"
        renderTabBar={(props, DefaultTabBar) => (
          <div className="settings-sticky-header">
            <div className="page-heading"><div><div className="page-eyebrow">WORKSPACE SETTINGS</div><h1>通用配置</h1></div></div>
            <DefaultTabBar {...props} />
          </div>
        )}
        items={[{
          key: 'llm',
          label: 'LLM 配置',
          children: <>

      <section className="data-section">
        <div className="section-heading"><h2>提供商列表</h2><span>{config?.providers.length || 0} 个提供商</span></div>
        <Table
          scroll={{ x: 680 }}
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
        <section className="config-section config-fields"><h2>默认配置</h2>
          <Form.Item
            name="default_provider"
            label="默认提供商"
            rules={[{ required: true }]}
          >
            <Select placeholder="选择默认提供商">
              {config?.providers.map((p) => (
                <Option key={p.id} value={p.id}>
                  {p.name}
                </Option>
              ))}
            </Select>
          </Form.Item>

          <Form.Item
            name="default_model"
            label="默认模型"
            rules={[{ required: true }]}
          >
            <Input placeholder="claude-opus-5" />
          </Form.Item>

          <Form.Item name="use_stage_config" valuePropName="checked" label="启用阶段化配置">
            <Switch aria-label="启用阶段化配置" />
          </Form.Item>
        </section>

        <Form.Item noStyle shouldUpdate>
          {({ getFieldValue }) =>
            getFieldValue('use_stage_config') ? (
              <section className="config-section"><h2>阶段配置</h2><div className="stage-grid">
                {['analyze', 'propose', 'fix', 'verify'].map((stage) => (
                  <div
                    key={stage}
                    className="stage-fields"
                  >
                    <h3>{stage.toUpperCase()} 阶段</h3>
                    <Form.Item
                      name={['stage_config', stage, 'provider']}
                      label="提供商"
                    >
                      <Select placeholder="选择提供商">
                        {config?.providers.map((p) => (
                          <Option key={p.id} value={p.id}>
                            {p.name}
                          </Option>
                        ))}
                      </Select>
                    </Form.Item>

                    <Form.Item
                      name={['stage_config', stage, 'model']}
                      label="模型"
                    >
                      <Input placeholder="模型名称" />
                    </Form.Item>

                    <Space>
                      <Form.Item
                        name={['stage_config', stage, 'temperature']}
                        label="Temperature"
                      >
                        <InputNumber
                          min={0}
                          max={2}
                          step={0.1}
                          style={{ width: 120 }}
                        />
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
                ))}
              </div></section>
            ) : null
          }
        </Form.Item>

        <Form.Item>
          <Button type="primary" htmlType="submit" loading={loading} icon={<SaveOutlined />}>
            保存配置
          </Button>
        </Form.Item>
      </Form>
          </>,
        }, {
          key: 'prompts',
          label: '提示词工程',
          children: <Form form={promptForm} layout="vertical" onFinish={handleSave} disabled={!config || loading} initialValues={{ response_style: 'detailed' }}>
            <section className="config-section config-fields">
              <h2>Agent 回答风格</h2>
              <Form.Item name="response_style" label="回答详细程度" rules={[{ required: true }]} extra="控制根因分析、修复方案和修复说明的表达。各档位均保留必要依据、风险和验证要求，已生成的回答保持原样。">
                <Select options={[
                  { value: 'detailed', label: '具体 · 当前详细程度' },
                  { value: 'concise', label: '较简 · 保留关键原因和步骤' },
                  { value: 'minimal', label: '极简 · 只保留核心结论和方向' },
                ]} />
              </Form.Item>
              <Button type="primary" htmlType="submit" loading={loading} icon={<SaveOutlined />}>保存回答设置</Button>
            </section>
          </Form>,
        }, {
          key: 'yunxiao',
          label: '云效 MCP 配置',
          children: <Form form={yunxiaoForm} layout="vertical" onFinish={handleSaveYunxiao} disabled={!yunxiaoConfig || yunxiaoLoading}>
            <section className="config-section config-fields">
              <h2>云效连接</h2>
              <Form.Item name="api_base" label="API 地址" rules={[{ required: true }]}>
                <Select options={[{ value: 'https://openapi-rdc.aliyuncs.com', label: '云效 OpenAPI' }]} />
              </Form.Item>
              <Form.Item name="org_id" label="组织 ID" rules={[{ required: true, message: '请输入组织 ID' }]}>
                <Input placeholder="实际组织 ID" />
              </Form.Item>
              <Form.Item name="project_id" label="项目 ID" rules={[{ required: true, message: '请输入项目 ID' }]}>
                <Input placeholder="项目 ID，填写 * 表示全部项目" />
              </Form.Item>
              <Form.Item
                name="access_token"
                label={yunxiaoConfig?.token_configured ? 'Access Token（留空保持不变）' : 'Access Token'}
                rules={[{ required: !yunxiaoConfig?.token_configured, message: '请输入 Access Token' }]}
              >
                <Input.Password placeholder={yunxiaoConfig?.token_configured ? '已配置，留空保持不变' : '请输入云效 Token'} />
              </Form.Item>
              <Button type="primary" htmlType="submit" loading={yunxiaoLoading} icon={<SaveOutlined />}>保存云效配置</Button>
            </section>
          </Form>,
        }]}
      />
    </div>
  );
};

export default LLMConfigPage;
