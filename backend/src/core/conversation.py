"""持久化用户消息；修复执行器仅在工具边界读取，审批仍由专门接口处理。"""
import asyncio
import json
import re
import uuid

import aiosqlite

from ..config import get_settings
from .image_storage import _directory, _read_data_url
from .rich_input import save_rich_input

# 与执行器注册、消息接收和结束检查共用。单后端进程，与现有调度器一致。
locks = {}
active = {}


def is_resume_request(text):
    """仅识别明确的短指令，避免将“继续解释”或否定句当成执行授权。"""
    return bool(re.fullmatch(r'(?:请|麻烦)?(?:继续(?:修复|执行|运行|处理)?|恢复(?:修复|执行|运行|任务)|重新执行|重试)(?:一下|吧)?[。.!！]*', text.strip()))


def lock_for(agent_id):
    return locks.setdefault(agent_id, asyncio.Lock())


def db_path():
    return get_settings().database_url.replace('sqlite+aiosqlite:///', '')


async def begin_question(agent_id, question):
    decision_id = f'question-{uuid.uuid4().hex}'
    async with aiosqlite.connect(db_path()) as db:
        await db.execute("""INSERT INTO decisions (decision_id, bug_id, agent_id, stage, question, options)
            SELECT ?, current_bug_id, agent_id, 'question', ?, '[]' FROM agents WHERE agent_id=?""",
                         (decision_id, question, agent_id))
        await db.commit()
    return decision_id


async def finish_question(decision_id, *, answered=False):
    async with aiosqlite.connect(db_path()) as db:
        await db.execute("UPDATE decisions SET status=?, answered_at=CURRENT_TIMESTAMP WHERE decision_id=? AND status='pending'",
                         ('answered' if answered else 'timeout', decision_id))
        await db.commit()


async def append_assistant(agent_id, text):
    async with aiosqlite.connect(db_path()) as db:
        cursor = await db.execute("""INSERT INTO conversation_messages (bug_id, agent_id, role, text, status)
            SELECT current_bug_id, agent_id, 'assistant', ?, 'sent' FROM agents WHERE agent_id=?""", (text, agent_id))
        await db.commit()
        return cursor.lastrowid


async def add_user(agent_id, request):
    async with aiosqlite.connect(db_path()) as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute('SELECT * FROM agents WHERE agent_id=?', (agent_id,))).fetchone()
        if not row:
            raise KeyError(agent_id)
        current = await (await db.execute('SELECT assigned_agent_id FROM bugs WHERE bug_id=?', (row['current_bug_id'],))).fetchone()
        if not current or current[0] != agent_id:
            raise ValueError('请在该 Bug 的最新修复会话中发送消息')
        prior = await (await db.execute('SELECT id FROM conversation_messages WHERE agent_id=? AND client_id=?', (agent_id, request.client_id))).fetchone()
        if prior:
            return prior[0]
        text, images, document = save_rich_input(db_path(), request.text, request.images, request.document, max_length=4000)
        if not text and not images:
            raise ValueError('请输入说明或粘贴截图')
        cursor = await db.execute('''INSERT INTO conversation_messages
            (bug_id, agent_id, role, text, images, document, client_id) VALUES (?, ?, 'user', ?, ?, ?, ?)''',
            (row['current_bug_id'], agent_id, text, json.dumps(images), json.dumps(document) if document else None, request.client_id))
        await db.commit()
        return cursor.lastrowid


async def messages(agent_id, *, load_images=True):
    async with aiosqlite.connect(db_path()) as db:
        db.row_factory = aiosqlite.Row
        agent = await (await db.execute('SELECT current_bug_id, status FROM agents WHERE agent_id=?', (agent_id,))).fetchone()
        if not agent:
            raise KeyError(agent_id)
        rows = await (await db.execute('SELECT * FROM conversation_messages WHERE bug_id=? ORDER BY id', (agent['current_bug_id'],))).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item['images'] = [url for name in json.loads(item['images']) if (url := _read_data_url(_directory(db_path()), name))] if load_images else json.loads(item['images'])
        item['document'] = json.loads(item['document']) if item['document'] else None
        result.append(item)
    return result


async def pending(agent_id):
    if not agent_id:
        return []
    async with aiosqlite.connect(db_path()) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute("SELECT * FROM conversation_messages WHERE agent_id=? AND role='user' AND status='queued' ORDER BY id", (agent_id,))).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item['images'] = [url for name in json.loads(item['images']) if (url := _read_data_url(_directory(db_path()), name))]
        result.append(item)
    return result


async def delivered(agent_id, ids):
    if not ids:
        return
    async with aiosqlite.connect(db_path()) as db:
        await db.executemany("UPDATE conversation_messages SET status='delivered' WHERE id=? AND agent_id=? AND status='queued'", [(i, agent_id) for i in ids])
        await db.commit()


def user_content(item):
    text = f"用户补充（消息 {item['id']}）：\n{item['text']}\n这是对当前任务的补充，不代表批准方案或合并。"
    if not item['images']:
        return text
    return [{'type': 'text', 'text': text}] + [{'type': 'image_url', 'image_url': {'url': url}} for url in item['images']]


async def history_context(agent_id):
    # 新执行轮次也能看到用户对前一轮的纠正；原图保留在会话中。
    rows = await messages(agent_id, load_images=False)
    return '\n\n会话中的用户补充：\n' + '\n'.join(f"[消息 {r['id']}，{len(r['images'])} 张截图] {r['text']}" for r in rows[-60:] if r['role'] == 'user')


async def image_data(agent_id, message_id, index):
    async with aiosqlite.connect(db_path()) as db:
        row = await (await db.execute('SELECT m.images FROM conversation_messages m JOIN agents a ON a.current_bug_id=m.bug_id WHERE a.agent_id=? AND m.id=?', (agent_id, message_id))).fetchone()
    names = json.loads(row[0]) if row else []
    if type(index) is not int or not 0 <= index < len(names):
        raise ValueError('截图不存在')
    url = _read_data_url(_directory(db_path()), names[index])
    if not url:
        raise ValueError('截图文件已不存在')
    return url
