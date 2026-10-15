import React, { useState, useEffect } from 'react';
import {
  Card,
  Radio,
  Switch,
  InputNumber,
  Button,
  Space,
  Alert,
  Divider,
  Tag,
  message,
  Spin,
  Collapse,
  Input,
} from 'antd';
import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  ThunderboltOutlined,
  SafetyOutlined,
  ExperimentOutlined,
  ReloadOutlined,
  SaveOutlined,
} from '@ant-design/icons';

const { Panel } = Collapse;
const { TextArea } = Input;

interface ConfirmationRules {
  require_on_low_confidence: boolean;
  require_on_multiple_proposals: boolean;
  multiple_proposals_threshold: number;
  require_on_high_risk: boolean;
  high_risk_keywords: string[];
  require_on_large_changes: boolean;
  large_changes_threshold: number;
  require_on_critical_priority: boolean;
  require_on_open_questions: boolean;
  require_on_schema_changes: boolean;
  schema_keywords: string[];
}

interface ConfirmationConfig {
  mode: 'always' | 'smart' | 'never';
  rules: ConfirmationRules;
  auto_approve_patterns: string[];
  force_confirm_patterns: string[];
}

export const ConfirmationSettings: React.FC = () => {
  const [config, setConfig] = useState<ConfirmationConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    loadConfig();
  }, []);

  const loadConfig = async () => {
    try {
      setLoading(true);
      const response = await fetch('http://localhost:8000/api/confirmation/config');
      if (response.ok) {
        const data = await response.json();
        setConfig(data);
      } else {
        message.error('加载配置失败');
      }
    } catch (error) {
      message.error('加载配置失败：' + error);
    } finally {
      setLoading(false);
    }
  };

  const saveConfig = async () => {
    if (!config) return;

    try {
      setSaving(true);
      const response = await fetch('http://localhost:8000/api/confirmation/config', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(config),
      });

      if (response.ok) {
        message.success('配置已保存');
      } else {
        message.error('保存配置失败');
      }
    } catch (error) {
      message.error('保存配置失败：' + error);
    } finally {
      setSaving(false);
    }
  };

  const resetConfig = async () => {
    try {
      setSaving(true);
      const response = await fetch('http://localhost:8000/api/confirmation/config/reset', {
        method: 'POST',
      });

      if (response.ok) {
        const data = await response.json();
        setConfig(data);
        message.success('配置已重置为默认');
      } else {
        message.error('重置配置失败');
      }
    } catch (error) {
      message.error('重置配置失败：' + error);
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div style={{ textAlign: 'center', padding: '100px 0' }}>
        <Spin size="large" />
      </div>
    );
  }

  if (!config) {
    return (
      <Alert
        message="加载配置失败"
        description="无法加载确认策略配置"
        type="error"
        showIcon
      />
    );
  }

  return (
    <div style={{ maxWidth: 1000, margin: '0 auto', padding: '24px' }}>
      <div style={{ marginBottom: 24 }}>
        <h2 style={{ marginBottom: 8 }}>方案确认策略</h2>
        <p style={{ color: 'var(--muted)', marginBottom: 0 }}>
          配置 Agent 在完成分析后是否需要用户确认修复方案
        </p>
      </div>

      {/* 确认模式选择 */}
      <Card style={{ marginBottom: 24 }}>
        <h3 style={{ marginBottom: 16 }}>确认模式</h3>
        <Radio.Group
          value={config.mode}
          onChange={(e) => setConfig({ ...config, mode: e.target.value })}
          style={{ width: '100%' }}
        >
          <Space direction="vertical" size="middle" style={{ width: '100%' }}>
            <Radio value="always">
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <SafetyOutlined style={{ fontSize: 16, color: '#52c41a' }} />
                <strong>总是确认（保守）</strong>
              </div>
              <div style={{ marginLeft: 24, color: 'var(--muted)', fontSize: 13 }}>
                每个方案都需要人工确认，适合生产环境和高风险项目
              </div>
            </Radio>

            <Radio value="smart">
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <ExperimentOutlined style={{ fontSize: 16, color: '#1890ff' }} />
                <strong>智能确认（推荐）</strong>
                <Tag color="blue">默认</Tag>
              </div>
              <div style={{ marginLeft: 24, color: 'var(--muted)', fontSize: 13 }}>
                根据置信度、风险级别等因素智能判断是否需要确认
              </div>
            </Radio>

            <Radio value="never">
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <ThunderboltOutlined style={{ fontSize: 16, color: '#faad14' }} />
                <strong>从不确认（激进）</strong>
              </div>
              <div style={{ marginLeft: 24, color: 'var(--muted)', fontSize: 13 }}>
                所有方案自动执行，适合开发环境和测试环境
              </div>
            </Radio>
          </Space>
        </Radio.Group>
      </Card>

      {/* 智能确认规则（仅在 smart 模式下显示）*/}
      {config.mode === 'smart' && (
        <Card style={{ marginBottom: 24 }}>
          <h3 style={{ marginBottom: 16 }}>智能确认规则</h3>
          <p style={{ color: 'var(--muted)', fontSize: 13, marginBottom: 16 }}>
            当满足以下任一条件时，将要求用户确认方案
          </p>

          <Space direction="vertical" size="middle" style={{ width: '100%' }}>
            {/* 置信度规则 */}
            <div>
              <Switch
                checked={config.rules.require_on_low_confidence}
                onChange={(checked) =>
                  setConfig({
                    ...config,
                    rules: { ...config.rules, require_on_low_confidence: checked },
                  })
                }
              />
              <span style={{ marginLeft: 12 }}>置信度低时需要确认（无复现测试）</span>
            </div>

            {/* 方案数量规则 */}
            <div>
              <Switch
                checked={config.rules.require_on_multiple_proposals}
                onChange={(checked) =>
                  setConfig({
                    ...config,
                    rules: { ...config.rules, require_on_multiple_proposals: checked },
                  })
                }
              />
              <span style={{ marginLeft: 12 }}>多个方案时需要确认（超过</span>
              <InputNumber
                min={1}
                max={10}
                value={config.rules.multiple_proposals_threshold}
                onChange={(value) =>
                  setConfig({
                    ...config,
                    rules: { ...config.rules, multiple_proposals_threshold: value || 2 },
                  })
                }
                size="small"
                style={{ width: 60, margin: '0 4px' }}
                disabled={!config.rules.require_on_multiple_proposals}
              />
              <span>个方案）</span>
            </div>

            {/* 高风险规则 */}
            <div>
              <Switch
                checked={config.rules.require_on_high_risk}
                onChange={(checked) =>
                  setConfig({
                    ...config,
                    rules: { ...config.rules, require_on_high_risk: checked },
                  })
                }
              />
              <span style={{ marginLeft: 12 }}>高风险修改需要确认</span>
            </div>

            {/* 改动范围规则 */}
            <div>
              <Switch
                checked={config.rules.require_on_large_changes}
                onChange={(checked) =>
                  setConfig({
                    ...config,
                    rules: { ...config.rules, require_on_large_changes: checked },
                  })
                }
              />
              <span style={{ marginLeft: 12 }}>大范围改动需要确认（超过</span>
              <InputNumber
                min={1}
                max={50}
                value={config.rules.large_changes_threshold}
                onChange={(value) =>
                  setConfig({
                    ...config,
                    rules: { ...config.rules, large_changes_threshold: value || 5 },
                  })
                }
                size="small"
                style={{ width: 60, margin: '0 4px' }}
                disabled={!config.rules.require_on_large_changes}
              />
              <span>个文件）</span>
            </div>

            {/* 优先级规则 */}
            <div>
              <Switch
                checked={config.rules.require_on_critical_priority}
                onChange={(checked) =>
                  setConfig({
                    ...config,
                    rules: { ...config.rules, require_on_critical_priority: checked },
                  })
                }
              />
              <span style={{ marginLeft: 12 }}>关键优先级 bug 需要确认</span>
            </div>

            {/* 待确认问题规则 */}
            <div>
              <Switch
                checked={config.rules.require_on_open_questions}
                onChange={(checked) =>
                  setConfig({
                    ...config,
                    rules: { ...config.rules, require_on_open_questions: checked },
                  })
                }
              />
              <span style={{ marginLeft: 12 }}>有待确认问题时需要确认</span>
            </div>

            {/* 数据结构规则 */}
            <div>
              <Switch
                checked={config.rules.require_on_schema_changes}
                onChange={(checked) =>
                  setConfig({
                    ...config,
                    rules: { ...config.rules, require_on_schema_changes: checked },
                  })
                }
              />
              <span style={{ marginLeft: 12 }}>数据结构变更需要确认</span>
            </div>
          </Space>

          <Divider />

          {/* 高级规则 */}
          <Collapse ghost>
            <Panel header="高级规则（正则表达式模式匹配）" key="1">
              <Space direction="vertical" size="middle" style={{ width: '100%' }}>
                <div>
                  <h4 style={{ marginBottom: 8 }}>
                    <CheckCircleOutlined style={{ color: '#52c41a' }} /> 自动批准模式
                  </h4>
                  <p style={{ color: 'var(--muted)', fontSize: 12, marginBottom: 8 }}>
                    匹配这些模式的 bug 标题将自动执行（正则表达式，每行一个）
                  </p>
                  <TextArea
                    rows={3}
                    placeholder="例如：^UI-.*&#10;样式.*&#10;文案.*"
                    value={config.auto_approve_patterns.join('\n')}
                    onChange={(e) =>
                      setConfig({
                        ...config,
                        auto_approve_patterns: e.target.value
                          .split('\n')
                          .filter((p) => p.trim()),
                      })
                    }
                  />
                </div>

                <div>
                  <h4 style={{ marginBottom: 8 }}>
                    <CloseCircleOutlined style={{ color: '#ff4d4f' }} /> 强制确认模式
                  </h4>
                  <p style={{ color: 'var(--muted)', fontSize: 12, marginBottom: 8 }}>
                    匹配这些模式的 bug 标题将强制确认（正则表达式，每行一个）
                  </p>
                  <TextArea
                    rows={3}
                    placeholder="例如：.*生产.*&#10;.*线上.*&#10;.*PROD.*"
                    value={config.force_confirm_patterns.join('\n')}
                    onChange={(e) =>
                      setConfig({
                        ...config,
                        force_confirm_patterns: e.target.value
                          .split('\n')
                          .filter((p) => p.trim()),
                      })
                    }
                  />
                </div>
              </Space>
            </Panel>
          </Collapse>
        </Card>
      )}

      {/* 操作按钮 */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <Button icon={<ReloadOutlined />} onClick={resetConfig} loading={saving}>
          重置为默认
        </Button>

        <Button
          type="primary"
          icon={<SaveOutlined />}
          onClick={saveConfig}
          loading={saving}
          size="large"
        >
          保存配置
        </Button>
      </div>

      {/* 说明信息 */}
      <Alert
        message="提示"
        description={
          <div>
            <p style={{ marginBottom: 8 }}>
              <strong>智能确认模式</strong>：系统会根据以上规则自动判断是否需要用户确认。
            </p>
            <ul style={{ marginBottom: 0, paddingLeft: 20 }}>
              <li>简单、低风险的 bug（UI 样式、文案等）将自动执行</li>
              <li>复杂、高风险的 bug（数据库、认证、支付等）将需要确认</li>
              <li>自动批准模式优先级高于智能规则</li>
              <li>强制确认模式优先级最高</li>
            </ul>
          </div>
        }
        type="info"
        showIcon
        style={{ marginTop: 24 }}
      />
    </div>
  );
};
