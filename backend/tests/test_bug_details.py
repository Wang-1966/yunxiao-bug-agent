from unittest.mock import AsyncMock, patch

import pytest

from src.api.bugs import get_bug_details
from src.models.bug import Bug
from src.tools.yunxiao.openapi_client import YunxiaoError


@pytest.mark.asyncio
async def test_detail_unwraps_rich_text_without_changing_local_status():
    bug = Bug(bug_id="bug", title="Title", priority="normal", status="resolved", source="yunxiao")
    remote = {"description": '{"htmlValue":"<p>正文</p>"}', "formatType": "RICHTEXT", "status": {"name": "已关闭"}}
    with patch("src.api.bugs.get_bug", AsyncMock(return_value=bug)), patch("src.tools.yunxiao.openapi_client.YunxiaoClient.get_bug", AsyncMock(return_value=remote)), patch("src.core.bug_media.load_bug_media", AsyncMock(return_value={})):
        result = await get_bug_details("bug")
    assert result["description"] == "<p>正文</p>"
    assert result["status"] == "resolved"


@pytest.mark.asyncio
async def test_detail_failure_preserves_local_data_with_warning():
    bug = Bug(bug_id="bug", title="Title", description="cached", priority="normal", source="yunxiao")
    with patch("src.api.bugs.get_bug", AsyncMock(return_value=bug)), patch("src.tools.yunxiao.openapi_client.YunxiaoClient.get_bug", AsyncMock(side_effect=YunxiaoError("超时"))):
        result = await get_bug_details("bug")
    assert result["description"] == "cached"
    assert "超时" in result["detail_warning"]
