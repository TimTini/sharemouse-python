"""Text WebSocket client and server for the LAN link. Stdlib only.

One complete text frame per message. A peer that splits a message across
frames is rejected; the sender in this file never does that.
Pass an SSL context to serve or connect to run the same frames over TLS.
"""
import asyncio
import base64
import hashlib
import ipaddress
import os
import platform
import socket
import ssl
import subprocess
from pathlib import Path

GUID = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11'


def run(coroutine):
    # TCP only, at most 32 concurrent discovery sockets / four control peers.
    # Windows Proactor shutdown can fail before detaching closed clients.
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        return runner.run(coroutine)


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
    if uri.startswith('wss://'):
        secure, rest = True, uri[6:]
    elif uri.startswith('ws://'):
        secure, rest = False, uri[5:]
    else:
        raise WebSocketException('Only ws:// and wss:// addresses are supported.')
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
    return host, port, secure


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
        self._peer_closed = False

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
                self._peer_closed = True
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

    async def close(self, *, wait_peer=False):
        if self._closed:
            return
        self._closed = True
        try:
            self.writer.write(_encode_frame(0x8, b'', self._mask))
            await asyncio.wait_for(self.writer.drain(), 0.5)
            if wait_peer and not self._peer_closed:
                # A completed handler owns the reader. Finish the WebSocket
                # close handshake before TCP shutdown can discard its last reply.
                async with asyncio.timeout(0.5):
                    while True:
                        await self.recv()
        except (OSError, TimeoutError, WebSocketException):
            pass
        finally:
            try:
                self.writer.close()
                await asyncio.wait_for(self.writer.wait_closed(), 0.5)
            except (OSError, TimeoutError):
                pass


async def _headers(reader):
    try:
        raw = await reader.readuntil(b'\r\n\r\n')
    except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError, OSError) as error:
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
    def __init__(self, handler, host, port, ssl_context=None, max_size=65536):
        self._handler = handler
        self._host = host
        self._port = port
        self._ssl = ssl_context
        self._server = None
        self.sockets = []
        self._max_size = max_size
        self._clients = set()

    async def __aenter__(self):
        options = {}
        if self._ssl is not None:
            options['ssl'] = self._ssl
            options['ssl_handshake_timeout'] = 5
        self._server = await asyncio.start_server(self._on_client, self._host, self._port, limit=8192, **options)
        self.sockets = list(self._server.sockets)
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self._server.close()
        tasks = list(self._clients)
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=1)
            for task in pending:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        await self._server.wait_closed()
        return False

    async def _on_client(self, reader, writer):
        peer = writer.get_extra_info('peername')
        if not peer or not is_local_ip(peer[0]):
            writer.close()
            return
        _nodelay(writer)
        task = asyncio.current_task()
        self._clients.add(task)
        socket = None
        try:
            await asyncio.wait_for(_accept(reader, writer), 3)
            socket = Socket(reader, writer, writer.get_extra_info('peername'), self._max_size, mask=False)
            await self._handler(socket)
        except (ConnectionClosed, WebSocketException, TimeoutError):
            return
        finally:
            try:
                if socket is not None:
                    await socket.close(wait_peer=True)
                else:
                    writer.close()
                    try:
                        await asyncio.wait_for(writer.wait_closed(), 0.5)
                    except (OSError, TimeoutError):
                        pass
            finally:
                self._clients.discard(task)


class Connection:
    def __init__(self, uri, open_timeout=10, max_size=None, ssl_context=None):
        self._uri = uri
        self._open_timeout = open_timeout
        self._max_size = max_size
        self._ssl = ssl_context
        self._socket = None

    async def __aenter__(self):
        host, port, secure = _parse(self._uri)
        host = _local_host(host)
        if secure and self._ssl is None:
            raise WebSocketException('wss:// requires the paired TLS certificate.')
        if self._ssl is not None and not secure:
            raise WebSocketException('A TLS certificate cannot be used with a ws:// address.')
        writer = None
        try:
            async with asyncio.timeout(self._open_timeout):
                reader, writer = await asyncio.open_connection(
                    host, port, ssl=self._ssl, server_hostname=host if secure else None)
                _nodelay(writer)
                await _handshake(reader, writer, host, port)
        except BaseException:
            if writer is not None:
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


def serve(handler, host, port, ssl=None, max_size=65536):
    return Server(handler, host, port, ssl, max_size)


def connect(uri, open_timeout=10, max_size=None, ssl=None):
    return Connection(uri, open_timeout, max_size, ssl)


def default_tls_dir():
    return Path(__file__).resolve().parent / 'tls'


def resolve_tls_dir(tls_dir):
    return Path(tls_dir) if tls_dir is not None else default_tls_dir()


def peer_certs(directory):
    peers = Path(directory) / 'peers'
    if not peers.is_dir():
        return []
    return sorted(path for path in peers.glob('*.pem') if path.is_file())


def has_identity(directory):
    directory = Path(directory)
    return (directory / 'cert.pem').is_file() and (directory / 'key.pem').is_file()


def _certificate_der(text):
    try:
        return ssl.PEM_cert_to_DER_cert(text)
    except ValueError as error:
        raise ValueError('That file is not a PEM certificate.') from error


def fingerprint(cert_path):
    der = _certificate_der(Path(cert_path).read_text(encoding='utf-8'))
    hex_digest = hashlib.sha256(der).hexdigest()[:32]
    return ' '.join(hex_digest[index:index + 4] for index in range(0, 32, 4))


def _pem(label, der):
    body = base64.encodebytes(der).decode('ascii')
    return f'-----BEGIN {label}-----\n{body}-----END {label}-----\n'


def _run(command, env=None):
    try:
        result = subprocess.run(
            command, check=False, capture_output=True, text=True,
            encoding='utf-8', errors='replace', timeout=40, env=env,
        )
    except subprocess.TimeoutExpired as error:
        raise ValueError('Creating the TLS certificate timed out.') from error
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or 'certificate creation failed').strip()
        raise ValueError(detail.splitlines()[-1][:400])


def _generate_windows(cert_der, key_der, subject):
    script = r"""
$ErrorActionPreference = 'Stop'
$cert = New-SelfSignedCertificate -Subject $env:SHAREMOUSE_SUBJECT -CertStoreLocation 'Cert:\CurrentUser\My' -KeyAlgorithm RSA -KeyLength 2048 -HashAlgorithm SHA256 -KeyExportPolicy Exportable -NotAfter (Get-Date).AddYears(10) -KeyUsage DigitalSignature, KeyEncipherment, CertSign -TextExtension @('2.5.29.19={text}CA=true','2.5.29.37={text}1.3.6.1.5.5.7.3.1,1.3.6.1.5.5.7.3.2')
try {
    Export-Certificate -Cert $cert -FilePath $env:SHAREMOUSE_CERT_DER | Out-Null
    $rsa = [System.Security.Cryptography.X509Certificates.RSACertificateExtensions]::GetRSAPrivateKey($cert)
    try { $keyBytes = $rsa.ExportPkcs8PrivateKey() }
    catch { $keyBytes = $rsa.Key.Export([System.Security.Cryptography.CngKeyBlobFormat]::Pkcs8PrivateBlob) }
    [System.IO.File]::WriteAllBytes($env:SHAREMOUSE_KEY_DER, $keyBytes)
} finally {
    Remove-Item -LiteralPath $cert.PSPath -Force -DeleteKey
}
"""
    env = dict(os.environ)
    env['SHAREMOUSE_CERT_DER'] = str(cert_der)
    env['SHAREMOUSE_KEY_DER'] = str(key_der)
    # Same subject on two certificates makes OpenSSL trust the wrong one.
    env['SHAREMOUSE_SUBJECT'] = f'CN={subject}'
    _run(['powershell.exe', '-NoProfile', '-Command', script], env)


def _generate_identity(cert_path, key_path):
    if platform.system() != 'Windows':
        raise OSError('This build supports Windows only.')
    subject = f'sharemouse-{os.urandom(8).hex()}'
    cert_der = cert_path.with_suffix('.der')
    key_der = key_path.with_suffix('.der')
    try:
        _generate_windows(cert_der, key_der, subject)
        cert_path.write_text(_pem('CERTIFICATE', cert_der.read_bytes()), encoding='ascii')
        key_path.write_text(_pem('PRIVATE KEY', key_der.read_bytes()), encoding='ascii')
    finally:
        cert_der.unlink(missing_ok=True)
        key_der.unlink(missing_ok=True)


def ensure_identity(directory):
    """Create this computer's certificate and private key once. The key stays here."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'peers').mkdir(exist_ok=True)
    cert, key = directory / 'cert.pem', directory / 'key.pem'
    if cert.is_file() and key.is_file():
        return cert
    if cert.exists() or key.exists():
        raise ValueError(f'Incomplete TLS files in {directory}. Delete them and run configure.py again.')
    try:
        _generate_identity(cert, key)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
    except Exception:
        cert.unlink(missing_ok=True)
        key.unlink(missing_ok=True)
        raise
    return cert


def trust_certificate(directory, text):
    """Persist a public certificate after the pairing protocol authenticates it."""
    directory = Path(directory)
    der = _certificate_der(text)
    own = directory / 'cert.pem'
    if own.is_file() and _certificate_der(own.read_text(encoding='utf-8')) == der:
        raise ValueError("Cannot trust this computer as a remote peer. Pair with the other computer.")
    peers = directory / 'peers'
    peers.mkdir(parents=True, exist_ok=True)
    dest = peers / f'{hashlib.sha256(der).hexdigest()}.pem'
    created = not dest.exists()
    temporary = peers / (dest.name + '.tmp')
    temporary.write_text(ssl.DER_cert_to_PEM_cert(der), encoding='ascii')
    temporary.replace(dest)
    return dest, created


def tls_context(directory, server):
    directory = Path(directory)
    cert, key = directory / 'cert.pem', directory / 'key.pem'
    if not cert.is_file() or not key.is_file():
        raise ValueError(f'Missing TLS certificate in {directory}. Run configure.py on this computer.')
    # Own certificate is trusted so this PC can probe its own server.
    # A remote computer still cannot connect without its private key.
    paths = [cert, *peer_certs(directory)]
    chunks = []
    for path in paths:
        text = path.read_text(encoding='utf-8')
        _certificate_der(text)
        chunks.append(text if text.endswith('\n') else text + '\n')
    protocol = ssl.PROTOCOL_TLS_SERVER if server else ssl.PROTOCOL_TLS_CLIENT
    context = ssl.SSLContext(protocol)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.check_hostname = False
    context.verify_mode = ssl.CERT_REQUIRED
    if hasattr(ssl, 'VERIFY_X509_TRUSTED_FIRST'):
        context.verify_flags |= ssl.VERIFY_X509_TRUSTED_FIRST
    context.load_cert_chain(cert, key)
    context.load_verify_locations(cadata=''.join(chunks))
    return context


def server_context(directory):
    return tls_context(directory, server=True)


def client_context(directory):
    return tls_context(directory, server=False)
