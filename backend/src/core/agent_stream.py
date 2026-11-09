"""模型思考过程：流式片段只保存在内存供前端轮询，单次请求结束后把完整思考写入 agent_logs。"""
import time

MAX_LIVE_CONTENT = 2000
LIVE = {}
TOOLS = {}


def tool_activity(agent_id):
    entries = list(TOOLS.get(agent_id, {}).values())
    if not entries:
        return None
    started_at = min(entry['started_at'] for entry in entries)
    return {'tools': list(dict.fromkeys(entry['name'] for entry in entries)),
            'started_at': started_at, 'elapsed_ms': round((time.time() - started_at) * 1000)}


def live_stream(agent_id):
    entry = LIVE.get(agent_id)
    return {**entry, 'elapsed_ms': round((time.time() - entry['started_at']) * 1000)} if entry else None


class ThinkingStream:
    """record(message, level, metadata) 负责落库；同一 Agent 同时只有一个模型请求。"""

    def __init__(self, agent_id, record):
        self.agent_id, self.record = agent_id, record

    def start(self, label):
        LIVE[self.agent_id] = {'label': label, 'thinking': '', 'content': '', 'content_chars': 0,
                               'started_at': time.time(), 'thinking_ms': 0}

    def tool_started(self, name):
        token = object()
        TOOLS.setdefault(self.agent_id, {})[token] = {'name': name, 'started_at': time.time()}
        return token

    def tool_finished(self, token):
        entries = TOOLS.get(self.agent_id, {})
        entries.pop(token, None)
        if not entries:
            TOOLS.pop(self.agent_id, None)

    async def reasoning(self, chunk):
        entry = LIVE.get(self.agent_id)
        if entry:
            entry['thinking'] += chunk
            entry['thinking_ms'] = round((time.time() - entry['started_at']) * 1000)

    async def content(self, chunk):
        entry = LIVE.get(self.agent_id)
        if entry:
            entry['content_chars'] += len(chunk)
            entry['content'] = (entry['content'] + chunk)[-MAX_LIVE_CONTENT:]

    async def finish(self):
        entry = LIVE.pop(self.agent_id, None)
        if entry and entry['thinking'].strip():
            await self.record(entry['thinking'].strip(), 'thinking', {'label': entry['label'], 'duration_ms': entry['thinking_ms']})

    async def say(self, text):
        if isinstance(text, str) and text.strip():
            from .conversation import append_assistant
            message_id = await append_assistant(self.agent_id, text.strip())
            await self.record(text.strip()[:2000], 'assistant', {'conversation_message_id': message_id})
