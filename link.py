"""Text WebSocket client and server for the LAN link. Stdlib only.

One complete text frame per message. A peer that splits a message across
frames is rejected; the sender in this file never does that.
"""
import asyncio
import base64
import hashlib
import ipaddress
import os
import socket

GUID = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11'


class WebSocketException(Exception):
    pass


class ConnectionClosed(WebSocketException):
    pass


def is_local_ip(host):
    """True for loopback, link-local, and private LAN addresses. No DNS."""
    try:
        ip = ipaddress.ip_address(str(host).split('%', 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if ip.is_unspecified or ip.is_multicast or ip.is_global:
        return False
    return bool(ip.is_loopback or ip.is_link_local or ip.is_private)


def _local_host(host):
    if host.lower() == 'localhost':
        return '127.0.0.1'
    if not is_local_ip(host):
        raise WebSocketException(
            'Refusing a public or named address. ShareMouse only talks to a local IP.'
        )
    return host


def _parse(uri):
    if not uri.startswith('ws://'):
        raise WebSocketException('Only ws:// addresses are supported.')
    rest = uri[5:]
    if rest.startswith('['):
        host, _, rest = rest[1:].partition(']')
        if rest.startswith(':'):
            rest = rest[1:]
    else:
        host, separator, rest = rest.partition(':')
        if not separator:
            raise WebSocketException('WebSocket address needs a port.')
    port_text = rest.split('/', 1)[0]
    try:
        port = int(port_text)
    except ValueError as error:
        raise WebSocketException('WebSocket port is not a number.') from error
    if not host or not 1 <= port <= 65535:
        raise WebSocketException('WebSocket address is invalid.')
    return host, port


def _encode_frame(opcode, payload, mask):
    head = bytearray([0x80 | opcode])
    length = len(payload)
    mark = 0x80 if mask else 0
    if length < 126:
        head.append(mark | length)
    elif length < 65536:
        head.append(mark | 126)
        head += length.to_bytes(2, 'big')
    else:
        head.append(mark | 127)
        head += length.to_bytes(8, 'big')
    if mask:
        key = os.urandom(4)
        head += key
        payload = bytes(byte ^ key[index % 4] for index, byte in enumerate(payload))
    return bytes(head) + payload


async def _read_exact(reader, size):
    try:
        return await reader.readexactly(size)
    except (asyncio.IncompleteReadError, ConnectionError, OSError) as error:
        raise ConnectionClosed() from error


async def _read_frame(reader, max_size):
    header = await _read_exact(reader, 2)
    opcode = header[0] & 0x0F
    length = header[1] & 0x7F
    if length == 126:
        length = int.from_bytes(await _read_exact(reader, 2), 'big')
    elif length == 127:
        length = int.from_bytes(await _read_exact(reader, 8), 'big')
    if max_size is not None and length > max_size:
        raise WebSocketException('Message is larger than allowed.')
    mask = await _read_exact(reader, 4) if header[1] & 0x80 else b''
    payload = await _read_exact(reader, length) if length else b''
    if mask:
        payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
    return opcode, payload


def _nodelay(writer):
    sock = writer.get_extra_info('socket')
    if sock is not None:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)


class Socket:
    def __init__(self, reader, writer, remote_address, max_size, mask):
        self.reader = reader
        self.writer = writer
        self.remote_address = remote_address
        self.max_size = max_size
        self._mask = mask
        self._closed = False

    async def send(self, message):
        data = message.encode('utf-8') if isinstance(message, str) else message
        await self._send_opcode(0x1, data)

    async def _send_opcode(self, opcode, payload):
        if self._closed:
            raise ConnectionClosed()
        self.writer.write(_encode_frame(opcode, payload, self._mask))
        await self.writer.drain()

    async def recv(self):
        while True:
            opcode, payload = await _read_frame(self.reader, self.max_size)
            if opcode == 0x1:
                return payload.decode('utf-8')
            if opcode == 0x8:
                raise ConnectionClosed()
            if opcode == 0x9:
                await self._send_opcode(0xA, payload)
                continue
            if opcode == 0xA:
                continue
            raise WebSocketException(f'Unsupported frame {opcode}.')

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return await self.recv()
        except ConnectionClosed:
            raise StopAsyncIteration

    async def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            self.writer.write(_encode_frame(0x8, b'', self._mask))
            await self.writer.drain()
        except (OSError, ConnectionClosed):
            pass
        try:
            self.writer.close()
        except OSError:
            pass


async def _headers(reader):
    try:
        raw = await reader.readuntil(b'\r\n\r\n')
    except (asyncio.IncompleteReadError, ConnectionError, OSError) as error:
        raise ConnectionClosed() from error
    if len(raw) > 8192:
        raise WebSocketException('WebSocket headers are too large.')
    lines = raw.decode('iso-8859-1').split('\r\n')
    headers = {}
    for line in lines[1:]:
        if not line:
            continue
        name, _, value = line.partition(':')
        headers[name.strip().lower()] = value.strip()
    return lines[0], headers


async def _accept(reader, writer):
    status, headers = await _headers(reader)
    key = headers.get('sec-websocket-key')
    if not status.startswith('GET ') or not key:
        raise WebSocketException('Not a WebSocket request.')
    accept = base64.b64encode(hashlib.sha1((key + GUID).encode('ascii')).digest()).decode('ascii')
    writer.write(
        'HTTP/1.1 101 Switching Protocols\r\n'
        'Upgrade: websocket\r\n'
        'Connection: Upgrade\r\n'
        f'Sec-WebSocket-Accept: {accept}\r\n'
        '\r\n'.encode('ascii')
    )
    await writer.drain()


async def _handshake(reader, writer, host, port):
    key = base64.b64encode(os.urandom(16)).decode('ascii')
    writer.write(
        f'GET / HTTP/1.1\r\n'
        f'Host: {host}:{port}\r\n'
        'Upgrade: websocket\r\n'
        'Connection: Upgrade\r\n'
        f'Sec-WebSocket-Key: {key}\r\n'
        'Sec-WebSocket-Version: 13\r\n'
        '\r\n'.encode('ascii')
    )
    await writer.drain()
    status, _headers_out = await _headers(reader)
    if ' 101 ' not in status:
        raise WebSocketException('Server rejected the WebSocket handshake.')


class Server:
    def __init__(self, handler, host, port):
        self._handler = handler
        self._host = host
        self._port = port
        self._server = None
        self.sockets = []

    async def __aenter__(self):
        self._server = await asyncio.start_server(self._on_client, self._host, self._port)
        self.sockets = list(self._server.sockets)
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self._server.close()
        await self._server.wait_closed()
        return False

    async def _on_client(self, reader, writer):
        peer = writer.get_extra_info('peername')
        if not peer or not is_local_ip(peer[0]):
            writer.close()
            return
        _nodelay(writer)
        socket = None
        try:
            await _accept(reader, writer)
            socket = Socket(reader, writer, writer.get_extra_info('peername'), None, mask=False)
            await self._handler(socket)
        finally:
            if socket is not None:
                await socket.close()
            else:
                writer.close()


class Connection:
    def __init__(self, uri, open_timeout=10, close_timeout=2, max_size=None):
        self._uri = uri
        self._open_timeout = open_timeout
        self._max_size = max_size
        self._socket = None

    async def __aenter__(self):
        host, port = _parse(self._uri)
        host = _local_host(host)
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), self._open_timeout,
            )
        except TimeoutError as error:
            raise TimeoutError('Timed out connecting to the server.') from error
        _nodelay(writer)
        try:
            await _handshake(reader, writer, host, port)
        except Exception:
            writer.close()
            raise
        self._socket = Socket(
            reader, writer, writer.get_extra_info('peername'), self._max_size, mask=True,
        )
        return self._socket

    async def __aexit__(self, exc_type, exc, tb):
        if self._socket is not None:
            await self._socket.close()
        return False


def serve(handler, host, port):
    return Server(handler, host, port)


def connect(uri, open_timeout=10, close_timeout=2, max_size=None):
    return Connection(uri, open_timeout=open_timeout, close_timeout=close_timeout, max_size=max_size)


async def _demo():
    seen = []

    async def handler(websocket):
        async for message in websocket:
            seen.append(message)
            await websocket.send('pong:' + message)
            return

    for blocked in ('ws://8.8.8.8:9', 'ws://example.com:9'):
        try:
            async with connect(blocked, open_timeout=0.2):
                raise AssertionError(blocked)
        except WebSocketException:
            pass
    async with serve(handler, '127.0.0.1', 0) as server:
        port = server.sockets[0].getsockname()[1]
        async with connect(f'ws://127.0.0.1:{port}', open_timeout=2, max_size=100) as websocket:
            await websocket.send('hi')
            reply = await websocket.recv()
    assert seen == ['hi'] and reply == 'pong:hi'


if __name__ == '__main__':
    asyncio.run(_demo())
    print('ok')
