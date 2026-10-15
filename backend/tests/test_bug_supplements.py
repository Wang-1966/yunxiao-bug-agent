import base64
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import aiosqlite
import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import bugs
from src.agent.nodes import build_agent_prompt, fetch_bug_node
from src.core import orchestrator
from src.core.bug_inputs import capture_bug
from src.core.bug_sync import sync_my_bugs
from src.core.bug_visual import visual_source
from src.models.bug import BugSupplementUpdate

PNG = 'data:image/png;base64,' + base64.b64encode(b'\x89PNG\r\n\x1a\nrepro').decode()


@pytest.fixture
async def supplement_db(tmp_path, monkeypatch):
    path = tmp_path / 'bugs.db'
    async with aiosqlite.connect(path) as db:
        await db.executescript(Path('src/db/init.sql').read_text())
        await db.execute("INSERT INTO bugs (bug_id, title, priority, source) VALUES ('one', '原标题', 'normal', 'yunxiao')")
        await db.commit()
    monkeypatch.setattr(bugs, 'get_db_path', lambda: str(path))
    monkeypatch.setattr(orchestrator.settings, 'database_url', f'sqlite+aiosqlite:///{path}')
    return path


async def save(notes, images=None, revision=0):
    return await bugs.save_bug_supplement('one', BugSupplementUpdate(notes=notes, images=images or [], revision=revision))


async def test_supplement_roundtrip_survives_sync_and_can_be_cleared(supplement_db):
    saved = await save('  分式后无法输入中文  ', [PNG, PNG])
    assert saved == {'notes': '分式后无法输入中文', 'images': [PNG], 'revision': 1}
    assert await bugs.get_bug_supplement('one') == saved
    assert await save(saved['notes'], saved['images'], revision=1) == saved
    remote = [{'id': 'one', 'subject': '新标题', 'description': '新云效正文'}]
    with patch('src.tools.yunxiao.openapi_client.YunxiaoClient.list_my_bugs', AsyncMock(return_value=remote)):
        await sync_my_bugs(orchestrator.settings)
    assert await bugs.get_bug_supplement('one') == saved
    assert (await bugs.get_bug('one')).description == '新云效正文'
    assert await save('', revision=1) == {'notes': '', 'images': [], 'revision': 2}
    # 移除当前截图不删除文件，正在执行的旧快照仍能读取。
    assert len(list((supplement_db.parent / 'feedback_images').iterdir())) == 1


async def test_http_validation_and_stale_edit_do_not_overwrite(supplement_db):
    app = FastAPI()
    app.include_router(bugs.router)
    with TestClient(app) as client:
        url = '/api/bugs/one/supplement'
        assert client.get(url).json()['revision'] == 0
        assert client.put(url, json={'notes': '新说明', 'images': [PNG], 'revision': 0}).status_code == 200
        assert client.put(url, json={'notes': '旧页面', 'revision': 0}).status_code == 409
        for payload in ({'notes': 'x' * 12001, 'revision': 1}, {'images': ['data:image/svg+xml;base64,AAAA'], 'revision': 1},
                        {'images': [PNG] * 5, 'revision': 1}, {'images': ['data:image/png;base64,bm90IHB uZw=='], 'revision': 1}):
            assert client.put(url, json=payload).status_code == 422
        assert client.put('/api/bugs/missing/supplement', json={'revision': 0}).status_code == 404
        assert client.get('/api/bugs/missing/supplement').status_code == 404
        assert client.get(url).json()['notes'] == '新说明'


async def test_queue_and_resume_freeze_notes_images_and_cloud_description(supplement_db):
    await save('第一轮说明', [PNG])
    runner = orchestrator.MultiAgentOrchestrator()
    runner._running = True
    task_id = await runner.submit_task('one', supplement_revision=1)
    _, _, task = runner.task_queue.get_nowait()
    await save('第二轮说明', revision=1)
    state = {'bug_id': 'one', 'input_snapshot': task.input_snapshot}
    fetch = AsyncMock(return_value={'title': '原标题', 'description': '原正文'})
    with patch('src.api.bugs.get_bug_details', fetch):
        initial = await fetch_bug_node(state)
    assert 'error' not in initial
    assert initial['bug_info']['supplement_notes'] == '第一轮说明'
    assert initial['bug_info']['agent_images'] == [PNG]
    agent_id = f'agent-{task_id}'
    runner.agent_states[agent_id]['state'].update(initial)
    await runner._save_status(agent_id, task, 'busy', 'analyzing', '已读取复现资料')
    async with aiosqlite.connect(supplement_db) as db:
        result = (await (await db.execute('SELECT result FROM task_history WHERE task_id=?', (task_id,))).fetchone())[0]
    assert PNG not in result
    restored = {'bug_id': 'one', **json.loads(result)}
    with patch('src.api.bugs.get_bug_details', AsyncMock(side_effect=AssertionError('续跑不应刷新原始正文'))):
        resumed = await fetch_bug_node(restored)
    assert resumed['bug_info']['description'] == '原正文'
    assert resumed['bug_info']['agent_images'] == [PNG]
    for stage, context in [('analyze', {'instructions': '无', 'inventory': 'app.py：1 个文件'}), ('fix', {'proposal': '方案', 'repro': '无'})]:
        prompt = build_agent_prompt(stage, {'bug_id': 'one', **resumed}, **context)
        assert '第一轮说明' in prompt and '第二轮说明' not in prompt
        assert '原图编号 1–1' in prompt
    original = visual_source(resumed['bug_info'])
    assert original != visual_source({**resumed['bug_info'], 'supplement_image_names': []})


async def test_submit_revision_conflict_rolls_back_claim(supplement_db):
    await save('新资料')
    runner = orchestrator.MultiAgentOrchestrator()
    runner._running = True
    with pytest.raises(ValueError, match='补充信息已更新'):
        await runner.submit_task('one', supplement_revision=0)
    assert runner.task_queue.empty()
    async with aiosqlite.connect(supplement_db) as db:
        assert await (await db.execute('SELECT status, assigned_agent_id FROM bugs')).fetchone() == ('pending', None)
        assert await (await db.execute('SELECT COUNT(*) FROM agents')).fetchone() == (0,)


async def test_updated_supplement_restarts_failed_agent_analysis(supplement_db):
    runner = orchestrator.MultiAgentOrchestrator()
    runner._running = True
    task_id = await runner.submit_task('one')
    _, _, task = runner.task_queue.get_nowait()
    agent_id = f'agent-{task_id}'
    runner.agent_states[agent_id]['state'].update(root_cause='旧推断', selected_proposal={'name': '旧方案'})
    await runner._save_status(agent_id, task, 'error', 'failed', '失败')
    await save('新的复现步骤', [PNG])
    result = await runner.retry_agent(agent_id)
    assert result['mode'] == 'restart' and result['agent_id'] != agent_id
    _, _, new_task = runner.task_queue.get_nowait()
    assert new_task.input_snapshot['supplement']['notes'] == '新的复现步骤'
    assert 'root_cause' not in runner.agent_states[result['agent_id']]['state']


async def test_remote_original_image_is_cached_and_missing_evidence_fails(supplement_db):
    async def respond(request):
        return httpx.Response(200, content=base64.b64decode(PNG.split(',')[1]), headers={'content-type': 'image/png'})
    original_client = httpx.AsyncClient
    with patch('src.core.bug_media.httpx.AsyncClient', side_effect=lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs)):
        snapshot = await capture_bug(str(supplement_db), {'title': '标题', 'description': '<img src="https://cdn.example/repro.png">'})
    names = snapshot['image_names']
    state = {'bug_id': 'one', 'input_snapshot': {'bug': snapshot, 'supplement': {'notes': '', 'image_names': [], 'revision': 0}}}
    loaded = await fetch_bug_node(state)
    assert loaded['bug_info']['agent_images'] == [PNG]
    (supplement_db.parent / 'feedback_images' / names[0]).unlink()
    failed = await fetch_bug_node(state)
    assert '截图文件缺失' in failed['error']


async def test_cloud_failure_keeps_existing_local_description_fallback(supplement_db):
    warning = '云效正文加载失败，当前显示本地信息'
    with patch('src.api.bugs.get_bug_details', AsyncMock(return_value={'title': '标题', 'description': '缓存正文', 'detail_warning': warning})):
        result = await fetch_bug_node({'bug_id': 'one'})
    assert result['bug_info']['description'] == '缓存正文'
    assert result['input_snapshot']['bug']['detail_warning'] == warning


async def test_missing_supplement_image_can_be_reuploaded_without_losing_notes(supplement_db):
    await save('保留文字', [PNG])
    for path in (supplement_db.parent / 'feedback_images').iterdir():
        path.unlink()
    loaded = await bugs.get_bug_supplement('one')
    assert loaded['notes'] == '保留文字' and loaded['images'] == []
    assert '缺失' in loaded['image_warning']
    restored = await save(loaded['notes'], [PNG], revision=loaded['revision'])
    assert restored['images'] == [PNG]
    assert 'image_warning' not in await bugs.get_bug_supplement('one')


async def test_existing_database_gets_supplement_table_without_losing_bugs(tmp_path, monkeypatch):
    from src.db import database
    path = tmp_path / 'legacy.db'
    async with aiosqlite.connect(path) as db:
        sql = Path('src/db/init.sql').read_text()
        await db.executescript(sql)
        await db.execute('DROP TABLE bug_supplements')
        await db.execute("INSERT INTO bugs (bug_id, title, priority, source, status) VALUES ('old', '旧 Bug', 'normal', 'manual', 'failed')")
        await db.commit()
    monkeypatch.setattr(database.settings, 'database_url', f'sqlite+aiosqlite:///{path}')
    await database.init_db()
    await database.init_db()
    async with aiosqlite.connect(path) as db:
        assert await (await db.execute('SELECT status FROM bugs')).fetchone() == ('failed',)
        assert await (await db.execute('SELECT COUNT(*) FROM bug_supplements')).fetchone() == (0,)
