import asyncio
import gzip
import socket

import aiohttp
import pytest
from aiohttp import web
from yarl import URL

from src.core.preview_gateway import PreviewGateway


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


@pytest.fixture
async def upstreams():
    runners = []
    ports = []
    for name in ('original', 'repair'):
        async def handler(request, name=name):
            if request.path == '/upload':
                return web.Response(body=await request.read(), headers={'Content-Encoding': request.headers['Content-Encoding']})
            if request.path == '/truncated':
                response = web.StreamResponse(headers={'Content-Length': '100'})
                await response.prepare(request)
                await response.write(b'partial')
                request.transport.close()
                return response
            if request.path == '/hmr':
                ws = web.WebSocketResponse(protocols=['vite-hmr'])
                await ws.prepare(request)
                await ws.send_str(name)
                async for message in ws:
                    if message.type == aiohttp.WSMsgType.TEXT:
                        await ws.send_str(message.data)
                    elif message.type == aiohttp.WSMsgType.BINARY:
                        await ws.send_bytes(message.data)
                return ws
            if request.path == '/compressed':
                return web.Response(body=gzip.compress(name.encode()), headers={'Content-Encoding': 'gzip'})
            if request.path == '/redirect':
                return web.Response(status=302, headers={'Location': f'http://127.0.0.1:{request.transport.get_extra_info("sockname")[1]}/destination?q=%2F'})
            response = web.json_response({
                'name': name, 'path': request.raw_path, 'host': request.host,
                'cookie': request.headers.get('Cookie'), 'body': (await request.read()).decode(),
            })
            response.headers.add('Set-Cookie', 'one=1; Path=/; HttpOnly')
            response.headers.add('Set-Cookie', 'two=2; Path=/')
            return response
        app = web.Application()
        app.router.add_route('*', '/{path:.*}', handler)
        runner = web.AppRunner(app, auto_decompress=False)
        await runner.setup()
        port = free_port()
        await web.TCPSite(runner, '127.0.0.1', port).start()
        runners.append(runner)
        ports.append(port)
    yield ports
    for runner in runners:
        await runner.cleanup()


async def test_gateway_preserves_request_response_and_switches_websockets(upstreams):
    gateway = PreviewGateway(free_port())
    await gateway.start()
    origin = f'http://localhost:{gateway.port}'
    try:
        async with aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar(), auto_decompress=False) as client:
            async with client.get(origin + '/not-ready') as response:
                assert response.status == 503
            await gateway.switch(upstreams[0])
            async with client.post(URL(origin + '/pre-test/path%2Fsegment?q=%2F&id=123', encoded=True), data='payload', headers={'Cookie': 'login=keep'}) as response:
                body = await response.json()
                assert body == {'name': 'original', 'path': '/pre-test/path%2Fsegment?q=%2F&id=123', 'host': f'localhost:{gateway.port}', 'cookie': 'login=keep', 'body': 'payload'}
                assert len(response.headers.getall('Set-Cookie')) == 2
            async with client.get(origin + '/compressed') as response:
                assert response.headers['Content-Encoding'] == 'gzip'
                assert gzip.decompress(await response.read()) == b'original'
            payload = gzip.compress(b'compressed upload')
            async with client.post(origin + '/upload', data=payload, headers={'Content-Encoding': 'gzip'}) as response:
                assert await response.read() == payload
            async with client.get(origin + '/truncated') as response:
                with pytest.raises(aiohttp.ClientPayloadError):
                    await response.read()
            async with client.get(origin + '/redirect', allow_redirects=False) as response:
                assert response.headers['Location'] == origin + '/destination?q=%2F'
            socket_before = await client.ws_connect(origin + '/hmr?token=kept', protocols=['vite-hmr'])
            assert socket_before.protocol == 'vite-hmr'
            assert (await socket_before.receive()).data == 'original'
            await socket_before.send_bytes(b'bytes')
            assert (await socket_before.receive()).data == b'bytes'
            await gateway.switch(upstreams[1])
            assert (await asyncio.wait_for(socket_before.receive(), 3)).type == aiohttp.WSMsgType.CLOSE
            async with client.ws_connect(origin + '/hmr', protocols=['vite-hmr']) as socket_after:
                assert (await socket_after.receive()).data == 'repair'
                await socket_after.send_str('hot-update')
                assert (await socket_after.receive()).data == 'hot-update'
            await gateway.switch(upstreams[0])
            async with client.get(origin + '/same-page?id=123') as response:
                assert (await response.json())['name'] == 'original'
            # 共享代理客户端不得保存其他浏览器会话的 Cookie。
            async with client.get(origin + '/cookie') as response:
                assert (await response.json())['cookie'] is None
    finally:
        await gateway.close()


async def test_unreachable_target_and_port_conflict_are_reported(upstreams):
    gateway = PreviewGateway(upstreams[0])
    with pytest.raises(OSError):
        await gateway.start()
    await gateway.close()
    gateway = PreviewGateway(free_port())
    await gateway.start()
    try:
        await gateway.switch(free_port())
        async with aiohttp.ClientSession() as client:
            async with client.get(f'http://127.0.0.1:{gateway.port}/') as response:
                assert response.status == 502
    finally:
        await gateway.close()
