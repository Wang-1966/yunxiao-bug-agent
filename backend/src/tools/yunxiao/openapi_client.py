"""云效 OpenAPI：同步缺陷及人工验收后的状态更新。"""
import json
from urllib.parse import urlparse, quote

import httpx

from ...config import Settings


class YunxiaoError(Exception):
    pass


class YunxiaoClient:
    def __init__(self, settings: Settings):
        self.settings = settings.model_copy()

    async def _get_workitem_resource(self, bug_id, resource):
        settings = self.settings
        if settings.yunxiao_api_base.rstrip('/') != "https://openapi-rdc.aliyuncs.com":
            raise YunxiaoError("YUNXIAO_API_BASE 应为 https://openapi-rdc.aliyuncs.com")
        async with httpx.AsyncClient(base_url=settings.yunxiao_api_base, headers={"x-yunxiao-token": settings.yunxiao_access_token}, timeout=30) as client:
            path = f"/oapi/v1/projex/organizations/{quote(settings.yunxiao_org_id, safe='')}/workitems/{quote(bug_id, safe='')}/{resource}"
            return self._json(await self._request(client, "GET", path))

    async def list_attachments(self, bug_id):
        items = await self._get_workitem_resource(bug_id, "attachments")
        if not isinstance(items, list):
            raise YunxiaoError("云效附件列表返回格式不符合预期")
        return items

    async def get_file(self, bug_id, file_id):
        item = await self._get_workitem_resource(bug_id, f"files/{quote(file_id, safe='')}")
        if not isinstance(item, dict) or not item.get("url"):
            raise YunxiaoError("云效文件接口未返回访问地址")
        return item

    async def get_bug(self, bug_id):
        settings = self.settings
        if settings.yunxiao_api_base.rstrip('/') != "https://openapi-rdc.aliyuncs.com":
            raise YunxiaoError("YUNXIAO_API_BASE 应为 https://openapi-rdc.aliyuncs.com")
        async with httpx.AsyncClient(base_url=settings.yunxiao_api_base, headers={"x-yunxiao-token": settings.yunxiao_access_token}, timeout=30) as client:
            response = await self._request(client, "GET", f"/oapi/v1/projex/organizations/{quote(settings.yunxiao_org_id, safe='')}/workitems/{quote(bug_id, safe='')}")
            item = self._json(response)
            if not isinstance(item, dict) or item.get("id") != bug_id or item.get("categoryId") != "Bug":
                raise YunxiaoError("云效详情返回格式不符合预期")
            return item

    async def _workflow_statuses(self, bug_id):
        item = await self.get_bug(bug_id)
        project_id = (item.get('space') or {}).get('id')
        type_id = (item.get('workitemType') or {}).get('id')
        if not project_id or not type_id:
            raise YunxiaoError('云效缺陷缺少项目或工作项类型，无法读取工作流状态')
        settings = self.settings
        root = f"/oapi/v1/projex/organizations/{quote(settings.yunxiao_org_id, safe='')}"
        async with httpx.AsyncClient(base_url=settings.yunxiao_api_base,
                                     headers={'x-yunxiao-token': settings.yunxiao_access_token}, timeout=30) as client:
            path = f"{root}/projects/{quote(project_id, safe='')}/workitemTypes/{quote(type_id, safe='')}/workflows"
            workflow = self._json(await self._request(client, 'GET', path))
        statuses = workflow.get('statuses') if isinstance(workflow, dict) else None
        if not isinstance(statuses, list):
            raise YunxiaoError('云效工作流状态列表返回格式不符合预期')
        return item, root, statuses

    async def list_bug_status_names(self, bug_id):
        _, _, statuses = await self._workflow_statuses(bug_id)
        names = []
        for status in statuses:
            if not isinstance(status, dict) or not status.get('id'):
                continue
            name = (status.get('displayName') or status.get('name') or '').strip()
            if name and name not in names:
                names.append(name)
        if not names:
            raise YunxiaoError('云效工作流没有可用状态')
        return names

    async def set_bug_status(self, bug_id, status_name):
        status_name = (status_name or '').strip()
        if not status_name:
            raise YunxiaoError('请选择云效状态')
        item, root, statuses = await self._workflow_statuses(bug_id)
        targets = [status for status in statuses if isinstance(status, dict) and status.get('id')
                   and (status.get('displayName') or status.get('name')) == status_name]
        if len(targets) != 1:
            raise YunxiaoError(f'云效工作流中未找到唯一的“{status_name}”状态，请检查项目工作流配置')
        target_id = targets[0]['id']
        if (item.get('status') or {}).get('id') != target_id:
            settings = self.settings
            async with httpx.AsyncClient(base_url=settings.yunxiao_api_base,
                                         headers={'x-yunxiao-token': settings.yunxiao_access_token}, timeout=30) as client:
                await self._request(client, 'PUT', f"{root}/workitems/{quote(bug_id, safe='')}", json={'status': target_id})
            item = await self.get_bug(bug_id)
            if (item.get('status') or {}).get('id') != target_id:
                raise YunxiaoError(f'云效状态更新后尚未确认为“{status_name}”，请重试')
        return {'status_id': target_id, 'source_status': status_name}

    async def set_bug_in_progress(self, bug_id):
        return await self.set_bug_status(bug_id, '处理中')

    async def list_my_bugs(self):
        settings = self.settings
        for field in ("yunxiao_access_token", "yunxiao_org_id", "yunxiao_project_id"):
            value = getattr(settings, field)
            if not value or value.startswith("your-"):
                raise YunxiaoError(f"请在通用配置的云效配置中填写 {field.upper()}")
        base = settings.yunxiao_api_base.rstrip("/")
        parsed = urlparse(base)
        if parsed.scheme != "https" or parsed.hostname != "openapi-rdc.aliyuncs.com" or parsed.path:
            raise YunxiaoError("YUNXIAO_API_BASE 应为 https://openapi-rdc.aliyuncs.com")

        async with httpx.AsyncClient(
            base_url=base,
            headers={"x-yunxiao-token": settings.yunxiao_access_token},
            timeout=30,
        ) as client:
            user_response = await self._request(client, "GET", "/oapi/v1/platform/user")
            user = self._json(user_response)
            if not isinstance(user, dict) or not user.get("id"):
                raise YunxiaoError("云效用户接口未返回用户 ID，无法按负责人同步")
            conditions = json.dumps({"conditionGroups": [[{
                "fieldIdentifier": "assignedTo", "operator": "CONTAINS",
                "value": [user["id"]], "toValue": None,
                "className": "user", "format": "list",
            }]]})
            path = f"/oapi/v1/projex/organizations/{quote(settings.yunxiao_org_id, safe='')}/workitems:search"
            if settings.yunxiao_project_id == "*":
                projects = await self._pages(client, path.replace("workitems:search", "projects:search"), {})
                project_ids = [project["id"] for project in projects]
            else:
                project_ids = [settings.yunxiao_project_id]
            items = {}
            for project_id in project_ids:
                batch = await self._pages(client, path, {
                    "category": "Bug", "spaceId": project_id,
                    "spaceType": "Project", "conditions": conditions,
                })
                for item in batch:
                    if not isinstance(item, dict) or not item.get("id") or not item.get("subject"):
                        raise YunxiaoError("云效缺陷缺少 ID 或标题，本次同步未写入")
                    if (item.get("assignedTo") or {}).get("id") != user["id"]:
                        raise YunxiaoError("云效返回了其他负责人数据，本次同步未写入")
                    if item.get("categoryId") != "Bug":
                        raise YunxiaoError("云效返回了非缺陷数据，本次同步未写入")
                    items[item["id"]] = item
            return list(items.values())

    async def _pages(self, client, path, body):
        items = []
        page = 1
        while True:
            response = await self._request(client, "POST", path, json={
                **body, "page": page, "perPage": 200, "orderBy": "gmtCreate", "sort": "desc",
            })
            batch = self._json(response)
            if not isinstance(batch, list):
                raise YunxiaoError("云效列表接口返回格式不符合预期")
            items.extend(batch)
            total_pages = response.headers.get("x-total-pages")
            if not batch or (total_pages and page >= int(total_pages)):
                break
            next_page = response.headers.get("x-next-page")
            if next_page is not None:
                if not next_page or next_page == "0":
                    break
                try:
                    next_number = int(next_page)
                except ValueError:
                    raise YunxiaoError("云效分页响应无效")
                if next_number <= page:
                    raise YunxiaoError("云效分页未前进，本次同步未写入")
                page = next_number
            elif len(batch) < 200:
                break
            else:
                page += 1
        return items

    @staticmethod
    def _json(response):
        try:
            return response.json()
        except ValueError:
            raise YunxiaoError("云效返回了非 JSON 响应，请检查 API 地址")

    @staticmethod
    async def _request(client, method, path, **kwargs):
        try:
            response = await client.request(method, path, **kwargs)
        except httpx.TimeoutException:
            raise YunxiaoError("云效请求超时，请稍后重试")
        except httpx.RequestError:
            raise YunxiaoError("无法连接云效 OpenAPI，请检查网络")
        if response.status_code >= 400:
            try:
                error_code = response.json().get("errorCode", "")
            except (ValueError, AttributeError):
                error_code = ""
            if error_code == "Forbidden.InvalidUser.UserNotInCurrentOrganization":
                raise YunxiaoError("当前账号不属于配置的组织，请检查 YUNXIAO_ORG_ID（需要实际组织 ID，而非组织简称）")
            messages = {
                401: "Token 无效或已过期", 403: "Token 或当前账号缺少工作项读写权限" if method != 'GET' else "Token 缺少用户、项目工作项或工作流读取权限",
                404: "组织或项目不存在，或当前账号无权访问", 429: "云效请求频率受限，请稍后重试",
            }
            raise YunxiaoError(messages.get(response.status_code, f"云效接口错误（HTTP {response.status_code}）"))
        return response
