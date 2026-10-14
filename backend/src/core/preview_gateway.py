"""本机固定入口代理；请求路径与浏览器来源不随工作树切换。"""
import asyncio
import errno
import socket

import aiohttp
from aiohttp import web
from multidict import CIMultiDict
from yarl import URL


HOP_HEADERS = {'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization',
               'te', 'trailer', 'transfer-encoding', 'upgrade'}
WS_HEADERS = {'sec-websocket-key', 'sec-websocket-version', 'sec-websocket-extensions',
              'sec-websocket-protocol', 'sec-websocket-accept'}


def forwarded_headers(headers, websocket=False):
    excluded = HOP_HEADERS | {part.strip().lower() for part in headers.get('Connection', '').split(',')}
    if websocket:
        excluded |= WS_HEADERS
    return CIMultiDict((key, value) for key, value in headers.items() if key.lower() not in excluded)


class PreviewGateway:
    def __init__(self, port):
        self.port = port
        self.target_port = None
        self._generation = 0
        self._runner = None
        self._client = None
        self._websockets = set()

    async def start(self):
        app = web.Application()
        app.router.add_route('*', '/{path:.*}', self._forward)
        self._runner = web.AppRunner(app, shutdown_timeout=3, auto_decompress=False)
        try:
            await self._runner.setup()
            await web.TCPSite(self._runner, '127.0.0.1', self.port).start()
            if socket.has_ipv6:
                try:
                    await web.TCPSite(self._runner, '::1', self.port).start()
                except OSError as exc:
                    if exc.errno not in (errno.EAFNOSUPPORT, errno.EADDRNOTAVAIL):
                        raise
            self._client = aiohttp.ClientSession(
                auto_decompress=False, cookie_jar=aiohttp.DummyCookieJar(),
                timeout=aiohttp.ClientTimeout(total=None, sock_connect=5),
                skip_auto_headers={'Accept-Encoding', 'Content-Type', 'User-Agent'},
            )
        except BaseException:
            await self.close()
            raise

    async def switch(self, target_port):
        if self.target_port == target_port:
            return
        self.target_port = target_port
        self._generation += 1
        # 旧 HMR 连接不能继续向已切换的页面推送原工作树内容。
        sockets = list(self._websockets)
        if sockets:
            await asyncio.gather(*(ws.close(code=1012, message=b'Preview source changed') for ws in sockets), return_exceptions=True)

    async def close(self):
        await self.switch(None)
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
        if self._client:
            await self._client.close()
            self._client = None

    async def _forward(self, request):
        port, generation = self.target_port, self._generation
        if port is None:
            raise web.HTTPServiceUnavailable(text='项目服务尚未就绪，请在项目管理查看启动状态。')
        target = URL(f'http://127.0.0.1:{port}{request.raw_path}', encoded=True)
        response = None
        try:
            if request.headers.get('Upgrade', '').lower() == 'websocket':
                return await self._websocket(request, target, generation)
            async with self._client.request(
                request.method, target, headers=forwarded_headers(request.headers),
                data=request.content.iter_any() if request.can_read_body else None,
                allow_redirects=False,
            ) as upstream:
                headers = forwarded_headers(upstream.headers)
                location = headers.get('Location')
                if location:
                    redirect = URL(location, encoded=True)
                    if redirect.host in ('127.0.0.1', 'localhost', '::1') and redirect.port == port:
                        headers['Location'] = str(redirect.with_host(URL(f'http://{request.host}').host).with_port(self.port))
                response = web.StreamResponse(status=upstream.status, reason=upstream.reason, headers=headers)
                await response.prepare(request)
                async for chunk in upstream.content.iter_any():
                    await response.write(chunk)
                await response.write_eof()
                return response
        except (aiohttp.ClientError, TimeoutError, OSError) as exc:
            if response is not None and response.prepared:
                # 响应头已发出后不能改发 502，关闭连接让客户端识别传输中断。
                response.force_close()
                if request.transport:
                    request.transport.close()
                return response
            raise web.HTTPBadGateway(text='项目服务连接失败，请检查启动日志。') from exc

    async def _websocket(self, request, target, generation):
        protocols = [part.strip() for part in request.headers.get('Sec-WebSocket-Protocol', '').split(',') if part.strip()]
        async with self._client.ws_connect(
            target, headers=forwarded_headers(request.headers, websocket=True),
            protocols=protocols, autoping=False, autoclose=False, max_msg_size=0,
        ) as upstream:
            if generation != self._generation:
                raise web.HTTPServiceUnavailable(text='预览已切换，请刷新页面。')
            downstream = web.WebSocketResponse(
                protocols=[upstream.protocol] if upstream.protocol else [],
                autoping=False, autoclose=False, compress=False, max_msg_size=0, timeout=3,
            )
            await downstream.prepare(request)
            self._websockets.add(downstream)
            if generation != self._generation:
                self._websockets.discard(downstream)
                await downstream.close(code=1012)
                return downstream
            tasks = [asyncio.create_task(self._relay(downstream, upstream)),
                     asyncio.create_task(self._relay(upstream, downstream))]
            try:
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            finally:
                self._websockets.discard(downstream)
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                await downstream.close()
            return downstream

    @staticmethod
    async def _relay(source, target):
        async for message in source:
            if message.type == aiohttp.WSMsgType.TEXT:
                await target.send_str(message.data)
            elif message.type == aiohttp.WSMsgType.BINARY:
                await target.send_bytes(message.data)
            elif message.type == aiohttp.WSMsgType.PING:
                await target.ping(message.data)
            elif message.type == aiohttp.WSMsgType.PONG:
                await target.pong(message.data)
            elif message.type == aiohttp.WSMsgType.CLOSE:
                await target.close(code=message.data or 1000, message=message.extra.encode())
                break
