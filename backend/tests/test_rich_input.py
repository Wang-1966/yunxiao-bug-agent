import base64
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import aiosqlite
import pytest
from fastapi import HTTPException

from src.api import bugs
from src.core import orchestrator
from src.core.rich_input import save_rich_input
from src.core.repair_review import confirm_repair
from src.models.bug import BugSupplementUpdate
from src.models.agent import RepairConfirmation

PNG = 'data:image/png;base64,' + base64.b64encode(b'\x89PNG\r\n\x1a\nrich-image').decode()


def document():
    return {'type': 'doc', 'content': [
        {'type': 'paragraph', 'content': [{'type': 'text', 'text': '复现步骤', 'marks': [{'type': 'bold'}]}]},
        {'type': 'image', 'attrs': {'image_index': 0, 'src': 'https://untrusted.example/image'}},
        {'type': 'paragraph', 'content': [{'type': 'text', 'text': '预期能继续输入'}]},
    ]}


@pytest.fixture
async def rich_db(tmp_path, monkeypatch):
    path = tmp_path / 'rich.db'
    async with aiosqlite.connect(path) as db:
        await db.executescript(Path('src/db/init.sql').read_text())
        await db.execute("INSERT INTO bugs (bug_id, title, source, priority) VALUES ('one', 'Bug', 'manual', 'normal')")
        await db.commit()
    monkeypatch.setattr(bugs, 'get_db_path', lambda: str(path))
    monkeypatch.setattr(orchestrator.settings, 'database_url', f'sqlite+aiosqlite:///{path}')
    return path


def test_document_preserves_order_and_uses_file_references(tmp_path):
    text, names, saved = save_rich_input(str(tmp_path / 'app.db'), '伪造摘要', [PNG], document(), max_length=4000)
    assert text == '复现步骤\n【本条信息的第 1 张截图】\n预期能继续输入'
    assert len(names) == 1 and PNG not in json.dumps(saved)
    assert 'untrusted' not in json.dumps(saved)
    assert saved['content'][0]['content'][0]['marks'] == [{'type': 'bold'}]
    assert saved['content'][1]['attrs'] == {'image_index': 0}


def test_supplement_document_has_no_fixed_character_limit(tmp_path):
    long_text = '补充说明' * 4000
    doc = {'type': 'doc', 'content': [{'type': 'paragraph', 'content': [{'type': 'text', 'text': long_text}]}]}
    text, _, saved = save_rich_input(str(tmp_path / 'app.db'), '', [], doc)
    assert text == long_text
    assert saved['content'][0]['content'][0]['text'] == long_text


@pytest.mark.parametrize('bad', [
    {'type': 'doc', 'content': [{'type': 'image', 'attrs': {'image_index': 3}}]},
    {'type': 'doc', 'content': [{'type': 'image', 'attrs': {'image_index': True}}]},
    {'type': 'doc', 'content': [{'type': 'paragraph', 'content': [{'type': 'text', 'text': 'a', 'marks': [{'type': 'link'}]}]}]},
    {'type': 'doc', 'content': [{'type': 'script'}]},
    {'type': 'doc', 'content': [{'type': 'paragraph', 'content': [{'type': 'text', 'text': 'a' * 4001}]}]},
    {'type': 'doc', 'content': [{'type': 'image', 'attrs': 'bad'}]},
])
def test_invalid_document_is_rejected_before_any_file_is_saved(tmp_path, bad):
    with pytest.raises(ValueError):
        save_rich_input(str(tmp_path / 'app.db'), '', [PNG], bad, max_length=4000)
    assert not (tmp_path / 'feedback_images').exists()


def test_repeated_image_positions_use_one_stored_reference(tmp_path):
    doc = document()
    doc['content'].append({'type': 'image', 'attrs': {'image_index': 1}})
    text, names, saved = save_rich_input(str(tmp_path / 'app.db'), '', [PNG, PNG], doc, max_length=4000)
    assert len(names) == 1 and text.count('第 1 张截图') == 2
    assert saved['content'][-1]['attrs']['image_index'] == 0


async def test_supplement_roundtrip_conflict_and_missing_image_keep_layout(rich_db):
    request = BugSupplementUpdate(images=[PNG], document=document(), revision=0)
    saved = await bugs.save_bug_supplement('one', request)
    assert (await bugs.get_bug_supplement('one')) == saved
    assert saved['document']['content'][1]['attrs'] == {'image_index': 0}
    assert '预期能继续输入' in saved['notes']
    with pytest.raises(HTTPException) as conflict:
        await bugs.save_bug_supplement('one', request)
    assert conflict.value.status_code == 409
    for file in (rich_db.parent / 'feedback_images').iterdir():
        file.unlink()
    loaded = await bugs.get_bug_supplement('one')
    assert loaded['document']['content'][1]['type'] == 'paragraph'
    assert '截图文件缺失' in loaded['document']['content'][1]['content'][0]['text']
    assert loaded['document']['content'][2]['content'][0]['text'] == '预期能继续输入'


async def test_proposal_and_verification_share_document_storage(rich_db):
    runner = orchestrator.MultiAgentOrchestrator()
    runner._running = True
    task_id = await runner.submit_task('one')
    task = runner.task_queue.get_nowait()[2]
    agent_id = f'agent-{task_id}'
    runner.agent_states[agent_id]['state'].update(worktree_path='/unused', proposals=[{'name': '修复'}], stage='awaiting_plan_confirmation')
    await runner._save_status(agent_id, task, 'awaiting_plan_confirmation', 'awaiting_plan_confirmation', '等待确认')
    with patch.object(runner, '_resume_proposal', AsyncMock()):
        await runner.confirm_proposal(agent_id, 0, images=[PNG], document=document())
        await runner.running_agents[agent_id]
    async with aiosqlite.connect(rich_db) as db:
        choice = json.loads((await (await db.execute("SELECT user_choice FROM decisions WHERE stage='proposal'")).fetchone())[0])
    assert choice['document']['content'][1]['attrs'] == {'image_index': 0}
    assert '预期能继续输入' in choice['feedback'] and PNG not in json.dumps(choice)
    runner.agent_states[agent_id]['state'].update(stage='awaiting_confirmation')
    await runner._save_status(agent_id, task, 'awaiting_confirmation', 'awaiting_confirmation', '等待验收')
    await confirm_repair(str(rich_db), agent_id, RepairConfirmation(confirmed=False, images=[PNG], document=document()))
    async with aiosqlite.connect(rich_db) as db:
        choice = json.loads((await (await db.execute("SELECT user_choice FROM decisions WHERE stage='verify'")).fetchone())[0])
    assert choice['document']['content'][0]['content'][0]['marks'] == [{'type': 'bold'}]
    assert len(choice['images']) == 1 and '预期能继续输入' in choice['feedback']


async def test_migration_adds_document_column_to_previous_supplement_table(rich_db, monkeypatch):
    from src.db import database
    async with aiosqlite.connect(rich_db) as db:
        await db.execute('DROP TABLE bug_supplements')
        await db.execute("CREATE TABLE bug_supplements (bug_id TEXT PRIMARY KEY, notes TEXT, image_names TEXT, revision INTEGER, updated_at TIMESTAMP)")
        await db.execute("INSERT INTO bug_supplements VALUES ('one', '旧说明', '[]', 2, CURRENT_TIMESTAMP)")
        await db.commit()
    monkeypatch.setattr(database.settings, 'database_url', f'sqlite+aiosqlite:///{rich_db}')
    await database.init_db()
    assert await bugs.get_bug_supplement('one') == {'notes': '旧说明', 'images': [], 'revision': 2}
