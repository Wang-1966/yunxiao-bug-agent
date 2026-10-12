from html.parser import HTMLParser
from urllib.parse import parse_qs, urlparse
import base64

import httpx

from ..tools.yunxiao.openapi_client import YunxiaoError


class FileReferences(HTMLParser):
    def __init__(self):
        super().__init__()
        self.files = {}

    def handle_starttag(self, tag, attrs):
        for name, value in attrs:
            if name not in ("src", "href", "poster") or not value:
                continue
            parsed = urlparse(value)
            if parsed.scheme == "https" and parsed.hostname == "devops.aliyun.com" and parsed.path == "/projex/api/workitem/file/url":
                file_id = parse_qs(parsed.query).get("fileIdentifier", [None])[0]
                if file_id:
                    self.files[value] = file_id


class ImageReferences(HTMLParser):
    def __init__(self):
        super().__init__()
        self.sources = []

    def handle_starttag(self, tag, attrs):
        if tag == 'img':
            source = dict(attrs).get('src')
            if source and source not in self.sources:
                self.sources.append(source)


def bug_image_urls(bug):
    """正文截图使用已解析的下载地址，不能把需登录的云效 URL 当作图片。"""
    references = ImageReferences()
    references.feed(bug.get('description') or '')
    urls = []
    for source in references.sources:
        url = (bug.get('media_urls') or {}).get(source, source)
        parsed = urlparse(url)
        if parsed.scheme != 'https' or parsed.hostname == 'devops.aliyun.com':
            raise ValueError('Bug 正文截图未成功加载，无法依据完整复现信息分析；请检查云效文件权限')
        if url not in urls:
            urls.append(url)
    return urls


async def agent_bug_images(bug):
    """云效签名地址很快过期，获取详情后立即读取，供整个修复流程使用。"""
    urls = bug_image_urls(bug)
    resolved = set((bug.get('media_urls') or {}).values())
    images = []
    async with httpx.AsyncClient(timeout=30) as client:
        for url in urls:
            if url not in resolved:
                images.append(url)
                continue
            try:
                async with client.stream('GET', url) as response:
                    response.raise_for_status()
                    mime = response.headers.get('content-type', '').split(';')[0].lower()
                    if mime not in ('image/png', 'image/jpeg', 'image/webp', 'image/gif'):
                        raise ValueError('Bug 截图格式不受支持，需 PNG、JPEG、WebP 或 GIF')
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > 20_000_000:
                            raise ValueError('Bug 截图超过 20MB，无法传入模型')
                    images.append(f'data:{mime};base64,' + base64.b64encode(content).decode('ascii'))
            except httpx.HTTPError as exc:
                raise ValueError(f'Bug 正文截图读取失败（{type(exc).__name__}），请检查网络及云效文件权限后重新执行') from exc
    return images


async def load_bug_media(client, bug_id, description):
    references = FileReferences()
    references.feed(description)
    warnings = []
    try:
        attachments = await client.list_attachments(bug_id)
    except YunxiaoError as exc:
        attachments = []
        warnings.append(f"附件列表加载失败：{exc}")
    files = {item.get("fileId") or item.get("id"): item for item in attachments}
    urls = {}
    for original, file_id in references.files.items():
        try:
            if file_id not in files:
                files[file_id] = await client.get_file(bug_id, file_id)
            if files[file_id].get("url"):
                urls[original] = files[file_id]["url"]
        except YunxiaoError as exc:
            warnings.append(f"正文文件加载失败：{exc}")
    return {"media_urls": urls, "attachments": attachments, "media_warning": "；".join(dict.fromkeys(warnings)) or None}
