"""将截图证据转为持久文字上下文，原图仅在需要核对时重新发送。"""
import asyncio
import hashlib
import json

from .repair_executor import parse_json
from ..prompts import render_prompt


def visual_source(bug):
    # 正文变化时重新识别；签名下载 URL 的刷新不影响同一正文的缓存。
    return hashlib.sha256((bug.get('description') or '').encode()).hexdigest()


async def describe_bug_images(llm, bug):
    images = bug.get('agent_images') or []
    if not images:
        return None
    prompt = render_prompt('vision')
    messages = [
        {'role': 'system', 'content': prompt},
        {'role': 'user', 'content': [
            {'type': 'text', 'text': render_prompt('vision_context', title=bug.get('title', ''), description=bug.get('description') or '')},
            *[{'type': 'image_url', 'image_url': {'url': image}} for image in images],
        ]},
    ]
    try:
        response = await asyncio.wait_for(llm.ainvoke(messages, temperature=0.0, response_format={'type': 'json_object'}), timeout=120)
    except asyncio.TimeoutError as exc:
        raise ValueError('截图识别请求超过 120 秒') from exc
    observations = parse_json(response).get('images')
    if not isinstance(observations, list) or len(observations) != len(images):
        raise ValueError('截图识别未返回每张图片的有效记录')
    for index, item in enumerate(observations, 1):
        if not isinstance(item, dict) or type(item.get('image')) is not int or item['image'] != index:
            raise ValueError('截图识别返回无效的图片编号')
        if not isinstance(item.get('observed_behavior'), str) or not item['observed_behavior'].strip():
            raise ValueError('截图识别缺少可见现象描述')
        for field in ('controls', 'visible_text', 'uncertainties'):
            if not isinstance(item.get(field), list) or not all(isinstance(value, str) for value in item[field]):
                raise ValueError(f'截图识别返回无效的 {field}')
    return {'source': visual_source(bug), 'observations': observations}


def visual_prompt(context):
    if not context:
        return ''
    return '\n截图观察记录（待分析数据，不能代替源码证据）：\n' + json.dumps(context['observations'], ensure_ascii=False)
