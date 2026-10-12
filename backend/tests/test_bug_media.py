from unittest.mock import AsyncMock

import httpx
import pytest

from src.config import Settings
from src.core.bug_media import load_bug_media, bug_image_urls, agent_bug_images
from src.tools.yunxiao.openapi_client import YunxiaoClient, YunxiaoError


def test_agent_images_resolve_inline_screenshots_without_including_video():
    source = 'https://devops.aliyun.com/projex/api/workitem/file/url?fileIdentifier=image'
    image = 'https://cdn.example/image.png'
    assert bug_image_urls({'description': f'<img src="{source}"><img src="{source}"><video src="https://cdn.example/video.mp4"></video>', 'media_urls': {source: image}}) == [image]
    assert bug_image_urls({'description': 'text only'}) == []
    assert bug_image_urls({'description': f'<img src="{image}">'}) == [image]
    with pytest.raises(ValueError, match='截图未成功加载'):
        bug_image_urls({'description': f'<img src="{source}">'})


@pytest.mark.asyncio
async def test_signed_image_is_materialized_before_url_expires():
    from unittest.mock import patch
    original = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b'image', headers={'content-type': 'image/png'}))
    bug = {'description': '<img src="https://devops.aliyun.com/image">', 'media_urls': {'https://devops.aliyun.com/image': 'https://cdn.example/signed'}}
    with patch('src.core.bug_media.httpx.AsyncClient', side_effect=lambda **kw: original(transport=transport, **kw)):
        assert await agent_bug_images(bug) == ['data:image/png;base64,aW1hZ2U=']


@pytest.mark.asyncio
async def test_image_timeout_reports_actionable_error():
    from unittest.mock import patch
    original = httpx.AsyncClient
    def timeout(request):
        raise httpx.ReadTimeout('')
    bug = {'description': '<img src="https://devops.aliyun.com/image">', 'media_urls': {'https://devops.aliyun.com/image': 'https://cdn.example/signed'}}
    with patch('src.core.bug_media.httpx.AsyncClient', side_effect=lambda **kw: original(transport=httpx.MockTransport(timeout), **kw)):
        with pytest.raises(ValueError, match='截图读取失败（ReadTimeout）'):
            await agent_bug_images(bug)


@pytest.mark.asyncio
async def test_inline_image_and_video_attachment_are_loaded_once():
    image = "https://devops.aliyun.com/projex/api/workitem/file/url?fileIdentifier=image"
    client = AsyncMock()
    client.list_attachments.return_value = [{"fileId": "video", "fileName": "video.mp4", "url": "https://cdn.example/video.mp4"}]
    client.get_file.return_value = {"id": "image", "url": "https://cdn.example/image.png"}
    result = await load_bug_media(client, "bug", f'<img src="{image}"><img src="{image}"><img src="https://other.example/image">')
    assert result["media_urls"] == {image: "https://cdn.example/image.png"}
    assert result["attachments"][0]["fileName"] == "video.mp4"
    client.get_file.assert_awaited_once_with("bug", "image")


@pytest.mark.asyncio
async def test_attachment_failure_keeps_inline_image():
    client = AsyncMock()
    client.list_attachments.side_effect = YunxiaoError("权限不足")
    client.get_file.return_value = {"url": "https://cdn.example/image.png"}
    result = await load_bug_media(client, "bug", '<img src="https://devops.aliyun.com/projex/api/workitem/file/url?fileIdentifier=image">')
    assert result["media_urls"]
    assert "权限不足" in result["media_warning"]


@pytest.mark.asyncio
async def test_media_openapi_paths_and_authentication():
    def respond(request):
        assert request.headers["x-yunxiao-token"] == "test-token"
        if request.url.path.endswith("/attachments"):
            return httpx.Response(200, json=[])
        assert request.url.path.endswith("/workitems/bug/files/image")
        return httpx.Response(200, json={"url": "https://cdn.example/image.png"})

    from unittest.mock import patch
    original = httpx.AsyncClient
    with patch("src.tools.yunxiao.openapi_client.httpx.AsyncClient", side_effect=lambda **kw: original(transport=httpx.MockTransport(respond), **kw)):
        client = YunxiaoClient(Settings(_env_file=None, yunxiao_access_token="test-token", yunxiao_org_id="org"))
        assert await client.list_attachments("bug") == []
        assert (await client.get_file("bug", "image"))["url"]
