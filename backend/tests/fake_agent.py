"""测试用假模型：按脚本返回原生工具调用，以及把旧式 operate 适配到新的分析/修复入口。"""
import json


class ScriptedLLM:
    """achat 按顺序返回脚本回复；回复为 callable 时以当前 messages 生成。"""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    async def achat(self, messages, tools, **kwargs):
        self.calls.append({'messages': json.loads(json.dumps(messages)), 'tools': [t['function']['name'] for t in tools], 'kwargs': kwargs})
        assert self.replies, '脚本回复已用完，模型被多调用了一次'
        reply = self.replies.pop(0)
        if callable(reply):
            reply = reply(messages)
        n = len(self.calls)
        return {'content': reply.get('content', ''), 'tool_calls': [
            {'id': f'call_{n}_{i}', 'type': 'function', 'function': {'name': name, 'arguments': args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)}}
            for i, (name, args) in enumerate(reply['calls'])]}

    async def ainvoke(self, *args, **kwargs):
        raise AssertionError('新流程不应再调用文本 ainvoke')


def turn(*calls, content=''):
    return {'content': content, 'calls': list(calls)}


def analysis_args(**overrides):
    args = {
        'symptom': {'expected': 'value 为 2', 'actual': 'value 为 1', 'steps': ['运行 app.py'], 'acceptance': ['value 等于 2']},
        'hypotheses': [{'description': 'app.py 赋值错误', 'evidence': 'app.py:1', 'status': 'confirmed'},
                       {'description': '断言写错', 'evidence': 'app.py:2', 'status': 'rejected'}],
        'root_cause': 'app.py:1 把 value 赋为 1',
        'trigger_paths': ['app.py:1'],
        'reproduction': {'command': '', 'test_files': [], 'not_reproducible_reason': '测试环境不运行真实页面'},
        'proposals': [{'name': 'correct value', 'steps': ['把 value 改为 2']}],
    }
    args.update(overrides)
    return ('submit_analysis', args)


REVIEW_OK = ('submit_review', {'refuted': False, 'summary': '证据成立'})


def review_refuted(location='app.py:1', detail='证据不足'):
    return ('submit_review', {'refuted': True, 'summary': '不成立', 'findings': [{'location': location, 'detail': detail}]})


PASSED_REVIEW = {'status': 'passed', 'refuted': False, 'summary': 'ok', 'findings': []}
NO_REPRO = {'command': None, 'test_files': [], 'hashes': {}, 'reproduced': False, 'exit_before': None, 'output_before': '', 'not_reproducible_reason': '测试'}


def adapt_operate(operate, proposals=None):
    """把旧式 operate(llm, path, prompt, writable=..., **kw) 适配为 run_analysis / run_fix 替身。"""
    async def analysis(llm, reviewer, path, prompt, **kwargs):
        result = await operate(llm, path, prompt, writable=False, image_urls=kwargs.get('images'), **kwargs)
        items = proposals or [{'name': '新方案', 'steps': ['修改']}]
        return {'root_cause': result['root_cause'], 'analysis': {'symptom': {}, 'root_cause': result['root_cause'], 'proposals': items},
                'repro': NO_REPRO, 'review': PASSED_REVIEW, 'confidence': result.get('confidence', 'high'),
                'proposals': items, 'source_context': {'reads': []}}

    async def fix(llm, reviewer, path, prompt, **kwargs):
        result = await operate(llm, path, prompt, writable=True, image_urls=kwargs.get('images'), **kwargs)
        return {'summary': result['summary'], 'review': PASSED_REVIEW, 'repro': NO_REPRO, 'source_context': {'reads': []}}

    return analysis, fix
