"""测试用假模型：按脚本返回原生工具调用，以及把旧式 operate 适配到新的分析/修复入口。"""
import json


SERVERS = []


class ScriptedLLM:
    """本地模拟模型服务；实际循环和工具执行使用真实 Pi SDK。"""
    def __init__(self, replies):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from types import SimpleNamespace
        import threading
        self.replies = [reply for reply in replies if reply.get('calls') != [('submit_review', {'refuted': False, 'summary': '证据成立'})]]
        self.calls = []
        model = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                model.calls.append({'messages': payload['messages'], 'tools': [t['function']['name'] for t in payload.get('tools', [])]})
                if not model.replies:
                    self.send_error(400, 'Script exhausted')
                    return
                reply = model.replies.pop(0)
                if callable(reply): reply = reply(payload['messages'])
                calls = []
                for index, (name, args) in enumerate(reply['calls']):
                    if name == 'read_file': name = 'read'
                    if name == 'edit_file':
                        args = dict(args)
                        old, new = args.pop('old_string'), args.pop('new_string')
                        name = 'edit' if old else 'write'
                        args.update({'edits': [{'oldText': old, 'newText': new}]} if old else {'content': new})
                    if name == 'submit_fix':
                        args = {**args, 'repair_summary': args.get('repair_summary', {'root_cause': '赋值不符合预期', 'solution': '修正赋值'})}
                    if name == 'submit_reply': args = {'action': 'none', **args}
                    calls.append({'index': index, 'id': f'call_{len(model.calls)}_{index}', 'type': 'function',
                                  'function': {'name': name, 'arguments': json.dumps(args)}})
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.end_headers()
                for chunk in [
                    {'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': reply.get('content', ''), 'tool_calls': calls}, 'finish_reason': None}]},
                    {'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'tool_calls' if calls else 'stop'}], 'usage': {'prompt_tokens': 10, 'completion_tokens': 10}},
                ]:
                    self.wfile.write(('data: ' + json.dumps(chunk) + '\n\n').encode())
                self.wfile.write(b'data: [DONE]\n\n')
                self.wfile.flush()
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        SERVERS.append(server)
        self.client = SimpleNamespace(provider=SimpleNamespace(base_url=f'http://127.0.0.1:{server.server_port}/v1', api_key='test-key', id='test', context_windows={'test-model': 64000}))
        self.model = 'test-model'
        self.defaults = {'max_tokens': 4096}

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
        'decision_questions': [], 'assumptions': [], 'verification_limits': [],
        'acceptance_checks': [{'criterion_index': 0, 'method': 'manual', 'detail': '运行 app.py，确认 value 为 2', 'test_files': []}],
        'proposals': [{'name': 'correct value', 'steps': ['把 value 改为 2'], 'files': ['app.py'],
                       'impact': {'categories': [], 'evidence': 'app.py:1 修正赋值，不改变接口'}}],
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
