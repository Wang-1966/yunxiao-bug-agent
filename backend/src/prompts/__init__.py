"""按名称加载提示词模板；任务数据只替换一次，避免被解释为模板。"""
from pathlib import Path
from string import Template

PROMPT_DIR = Path(__file__).resolve().parent


def render_prompt(name: str, **context) -> str:
    path = (PROMPT_DIR / f'{name}.txt').resolve()
    if not path.is_relative_to(PROMPT_DIR):
        raise ValueError('提示词模板必须位于 prompts 目录')
    return Template(path.read_text(encoding='utf-8')).substitute(context).strip()
