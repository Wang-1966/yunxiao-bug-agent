"""WebSocket API 路由"""
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from loguru import logger
from typing import Set
import json

router = APIRouter()


class ConnectionManager:
    """WebSocket 连接管理器"""

    def __init__(self):
        self.active_connections: Set[WebSocket] = set()

    async def connect(self, websocket: WebSocket):
        """接受新连接"""
        await websocket.accept()
        self.active_connections.add(websocket)
        logger.info(f"WebSocket connected. Total: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        """断开连接"""
        self.active_connections.discard(websocket)
        logger.info(f"WebSocket disconnected. Total: {len(self.active_connections)}")

    async def broadcast(self, message: dict):
        """广播消息给所有连接"""
        disconnected = set()
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception as e:
                logger.error(f"Failed to send message: {e}")
                disconnected.add(connection)

        # 清理断开的连接
        for conn in disconnected:
            self.disconnect(conn)

    async def send_to_client(self, websocket: WebSocket, message: dict):
        """发送消息给特定客户端"""
        try:
            await websocket.send_json(message)
        except Exception as e:
            logger.error(f"Failed to send message: {e}")
            self.disconnect(websocket)


# 全局连接管理器
manager = ConnectionManager()


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket 端点"""
    await manager.connect(websocket)

    try:
        while True:
            # 接收客户端消息
            data = await websocket.receive_text()
            message = json.loads(data)

            message_type = message.get("type")
            logger.debug(f"Received message: {message_type}")

            # 处理不同类型的消息
            if message_type == "ping":
                await manager.send_to_client(websocket, {
                    "type": "pong",
                    "timestamp": message.get("timestamp")
                })

            elif message_type == "user_decision":
                # 用户决策消息
                # TODO: 转发给等待的 Agent
                logger.info(f"User decision received: {message}")
                await manager.send_to_client(websocket, {
                    "type": "decision_ack",
                    "decision_id": message.get("decision_id")
                })

            elif message_type == "agent_control":
                # Agent 控制消息（暂停、恢复、停止）
                # TODO: 转发给 Orchestrator
                logger.info(f"Agent control received: {message}")
                await manager.send_to_client(websocket, {
                    "type": "control_ack",
                    "agent_id": message.get("agent_id"),
                    "action": message.get("action")
                })

            else:
                logger.warning(f"Unknown message type: {message_type}")

    except WebSocketDisconnect:
        manager.disconnect(websocket)
        logger.info("Client disconnected normally")

    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        manager.disconnect(websocket)


async def broadcast_agent_update(agent_id: str, data: dict):
    """广播 Agent 状态更新"""
    await manager.broadcast({
        "type": "agent_update",
        "agent_id": agent_id,
        "data": data
    })


async def broadcast_agent_log(agent_id: str, log: str, level: str = "info"):
    """广播 Agent 日志"""
    await manager.broadcast({
        "type": "agent_log",
        "agent_id": agent_id,
        "log": log,
        "level": level
    })


async def request_user_decision(agent_id: str, decision_data: dict):
    """请求用户决策"""
    await manager.broadcast({
        "type": "decision_request",
        "agent_id": agent_id,
        "data": decision_data
    })
