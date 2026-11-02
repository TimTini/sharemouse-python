"""Loopback WebSocket and mutual TLS checks; no input controllers."""
import asyncio
from pathlib import Path
import ssl

from link import (WebSocketException, connect, serve, ensure_identity, import_peer,
                  server_context, client_context)

async def check():
    seen = []

    async def handler(websocket):
        async for message in websocket:
            seen.append(message)
            await websocket.send('pong:' + message)
            return

    for blocked in ('ws://8.8.8.8:9', 'ws://example.com:9', 'wss://8.8.8.8:9'):
        try:
            async with connect(blocked, open_timeout=0.2):
                raise AssertionError(blocked)
        except WebSocketException:
            pass
    try:
        async with connect('wss://127.0.0.1:9', open_timeout=0.2):
            raise AssertionError('wss without a certificate')
    except WebSocketException:
        pass
    async with serve(handler, '127.0.0.1', 0) as server:
        port = server.sockets[0].getsockname()[1]
        async with connect(f'ws://127.0.0.1:{port}', open_timeout=2, max_size=100) as websocket:
            await websocket.send('hi')
            reply = await websocket.recv()
    assert seen == ['hi'] and reply == 'pong:hi'
    await check_tls()


async def _reject(uri, context):
    try:
        async with connect(uri, ssl=context, open_timeout=2):
            raise AssertionError(uri)
    except (ssl.SSLError, ConnectionError, WebSocketException):
        return


async def check_tls():
    import tempfile
    seen = []

    async def handler(websocket):
        async for message in websocket:
            seen.append(message)
            await websocket.send('pong:' + message)
            return

    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        server_dir, client_dir, other_dir, stranger_dir = (
            root / 'server', root / 'client', root / 'other', root / 'stranger',
        )
        ensure_identity(server_dir)
        ensure_identity(client_dir)
        ensure_identity(other_dir)
        ensure_identity(stranger_dir)
        try:
            import_peer(server_dir, server_dir / 'cert.pem')
            raise AssertionError('accepted this computer certificate as a peer')
        except ValueError:
            pass
        import_peer(server_dir, client_dir / 'cert.pem')
        import_peer(server_dir, other_dir / 'cert.pem')
        import_peer(client_dir, server_dir / 'cert.pem')
        import_peer(other_dir, server_dir / 'cert.pem')
        import_peer(stranger_dir, server_dir / 'cert.pem')
        async with serve(handler, '127.0.0.1', 0, ssl=server_context(server_dir)) as server:
            port = server.sockets[0].getsockname()[1]
            uri = f'wss://127.0.0.1:{port}'
            await _reject(uri, client_context(stranger_dir))
            async with connect(uri, ssl=client_context(server_dir), open_timeout=2) as websocket:
                await websocket.send('self')
                assert await websocket.recv() == 'pong:self'
            async with connect(uri, ssl=client_context(client_dir), open_timeout=2) as websocket:
                await websocket.send('hi')
                reply = await websocket.recv()
            async with connect(uri, ssl=client_context(other_dir), open_timeout=2) as websocket:
                await websocket.send('two')
                other = await websocket.recv()
        assert seen == ['self', 'hi', 'two'] and reply == 'pong:hi' and other == 'pong:two'
        async with serve(handler, '127.0.0.1', 0, ssl=server_context(stranger_dir)) as server:
            port = server.sockets[0].getsockname()[1]
            await _reject(f'wss://127.0.0.1:{port}', client_context(client_dir))


if __name__ == '__main__':
    asyncio.run(check())
    print('link and paired TLS: ok')
