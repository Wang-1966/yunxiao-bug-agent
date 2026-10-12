import { useEffect } from 'react';
import { wsService } from '../services/websocket';
import { useAgentStore } from '../stores/agentStore';
import { useBugStore } from '../stores/bugStore';

export const useWebSocket = () => {
  const { updateAgent } = useAgentStore();
  const { updateBug } = useBugStore();

  useEffect(() => {
    // 连接 WebSocket
    wsService.connect();

    // 订阅 Agent 更新
    const unsubscribeAgentUpdate = wsService.on('agent_update', (data) => {
      if (data.agent_id && data.data) {
        updateAgent(data.agent_id, data.data);
      }
    });

    // 订阅 Agent 日志
    const unsubscribeAgentLog = wsService.on('agent_log', (data) => {
      console.log(`[${data.agent_id}] ${data.log}`);
      // 可以将日志添加到 Agent 的日志列表中
    });

    // 订阅决策请求
    const unsubscribeDecisionRequest = wsService.on('decision_request', (data) => {
      console.log('Decision requested:', data);
      // 可以触发决策对话框
    });

    // 清理
    return () => {
      unsubscribeAgentUpdate();
      unsubscribeAgentLog();
      unsubscribeDecisionRequest();
      wsService.disconnect();
    };
  }, [updateAgent, updateBug]);

  return {
    sendDecision: wsService.sendDecision.bind(wsService),
    sendAgentControl: wsService.sendAgentControl.bind(wsService),
  };
};
