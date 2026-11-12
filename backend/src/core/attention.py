"""从当前任务归属和待决记录派生待办，避免历史任务重复要求用户操作。"""
from .repair_summary import repair_summary


def attention_item(agent, bug, state, decisions, confirmation):
    if not bug or bug.get('assigned_agent_id') != agent['agent_id']:
        return None
    status = agent['status']
    expected = {'awaiting_plan_confirmation': 'proposal', 'awaiting_confirmation': 'verify', 'busy': 'question'}
    pending = next((item for item in decisions if item['stage'] == expected.get(status) and item['status'] == 'pending'), None)
    if pending:
        kind = {'proposal': 'proposal', 'verify': 'acceptance', 'question': 'question'}[pending['stage']]
        summary = pending['question']
        if kind == 'proposal':
            summary = ((state.get('selected_proposal') or (state.get('proposals') or [{}])[0]).get('name') or summary)
        elif kind == 'acceptance':
            brief = repair_summary(state)
            summary = f"根因：{brief['root_cause']}\n修复方案：{brief['solution']}"
        return {'kind': kind, 'summary': summary, 'since': pending['created_at'], 'decision_id': pending['decision_id']}
    choice = (confirmation or {}).get('user_choice') or {}
    if choice.get('confirmed'):
        merge = choice.get('merge') or {}
        sync = choice.get('yunxiao_sync') or {}
        if merge.get('cleanup_pending') or sync.get('status') in ('pending', 'failed'):
            return {'kind': 'delivery', 'summary': merge.get('cleanup_error') or sync.get('error') or '代码已交付，清理或云效状态同步需要处理',
                    'since': (confirmation or {}).get('answered_at') or agent.get('updated_at')}
    if status == 'error':
        return {'kind': 'error', 'summary': state.get('error') or '任务已停止，请查看原因后继续修复', 'since': agent.get('updated_at')}
    return None
