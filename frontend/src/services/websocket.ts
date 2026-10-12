import type { WebSocketMessage } from '../types';

class WebSocketService {
  private ws: WebSocket | null = null;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private listeners: Map<string, Set<(data: any) => void>> = new Map();
  private url: string;
  private reconnectAttempts = 0;
  private maxReconnectAttempts = 5;

  constructor() {
    const wsUrl = import.meta.env.VITE_WS_URL || 'ws://localhost:8000/ws';
    this.url = wsUrl;
  }

  connect() {
    if (this.ws?.readyState === WebSocket.OPEN) {
      return;
    }

    try {
      this.ws = new WebSocket(this.url);

      this.ws.onopen = () => {
        console.log('WebSocket connected');
        this.reconnectAttempts = 0;

        // 发送 ping 保持连接
        this.startHeartbeat();
      };

      this.ws.onmessage = (event) => {
        try {
          const message: WebSocketMessage = JSON.parse(event.data);
          this.handleMessage(message);
        } catch (error) {
          console.error('Failed to parse WebSocket message:', error);
        }
      };

      this.ws.onclose = () => {
        console.log('WebSocket closed');
        this.stopHeartbeat();
        this.attemptReconnect();
      };

      this.ws.onerror = (error) => {
        console.error('WebSocket error:', error);
      };
    } catch (error) {
      console.error('Failed to create WebSocket:', error);
      this.attemptReconnect();
    }
  }

  disconnect() {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.stopHeartbeat();

    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
  }

  private attemptReconnect() {
    if (this.reconnectAttempts >= this.maxReconnectAttempts) {
      console.error('Max reconnect attempts reached');
      return;
    }

    const delay = Math.min(1000 * Math.pow(2, this.reconnectAttempts), 30000);
    this.reconnectAttempts++;

    console.log(`Reconnecting in ${delay}ms (attempt ${this.reconnectAttempts})`);
    this.reconnectTimer = setTimeout(() => {
      this.connect();
    }, delay);
  }

  private heartbeatTimer: ReturnType<typeof setInterval> | null = null;

  private startHeartbeat() {
    this.heartbeatTimer = setInterval(() => {
      this.send({
        type: 'ping',
        timestamp: Date.now(),
      });
    }, 30000); // 每 30 秒发送一次心跳
  }

  private stopHeartbeat() {
    if (this.heartbeatTimer) {
      clearInterval(this.heartbeatTimer);
      this.heartbeatTimer = null;
    }
  }

  send(message: any) {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(message));
    } else {
      console.warn('WebSocket not connected');
    }
  }

  // 订阅特定类型的消息
  on(type: string, callback: (data: any) => void) {
    if (!this.listeners.has(type)) {
      this.listeners.set(type, new Set());
    }
    this.listeners.get(type)!.add(callback);

    // 返回取消订阅函数
    return () => {
      this.listeners.get(type)?.delete(callback);
    };
  }

  private handleMessage(message: WebSocketMessage) {
    const { type, ...data } = message;

    // 触发特定类型的监听器
    const listeners = this.listeners.get(type);
    if (listeners) {
      listeners.forEach((callback) => callback(data));
    }

    // 触发通用监听器
    const allListeners = this.listeners.get('*');
    if (allListeners) {
      allListeners.forEach((callback) => callback(message));
    }
  }

  // 发送用户决策
  sendDecision(decisionId: string, choice: any) {
    this.send({
      type: 'user_decision',
      decision_id: decisionId,
      choice: choice,
    });
  }

  // 发送 Agent 控制命令
  sendAgentControl(agentId: string, action: 'pause' | 'resume' | 'stop') {
    this.send({
      type: 'agent_control',
      agent_id: agentId,
      action: action,
    });
  }
}

export const wsService = new WebSocketService();
