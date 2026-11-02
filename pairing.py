"""One-use LAN pairing: SPAKE2 confirms a PIN, then authenticates public certificates.

This listener never handles input events or file paths. Normal control still
requires mTLS. The PIN and PAKE keys are never written to disk or sent directly.
"""
import asyncio
import base64
import hashlib
import hmac
import json
import re
import secrets
import socket
import ssl
import time
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from spake2 import SPAKE2_A, SPAKE2_B, SPAKEError

import link

PROTOCOL = b'ShareMouse-pair-v1'
PAIR_LIFETIME = 120
MAX_ATTEMPTS = 5


class PairingError(ValueError):
    pass


async def _send(ws, kind, **fields):
    await ws.send(json.dumps({'type': kind, **fields}, separators=(',', ':')))


async def _recv(ws, expected=None):
    message = json.loads(await asyncio.wait_for(ws.recv(), 5))
    if not isinstance(message, dict) or message.get('type') == 'error':
        raise PairingError('Pairing failed. Verify the PIN or reopen pairing on the server.')
    if expected is not None and message.get('type') != expected:
        raise PairingError('Unexpected pairing response.')
    return message


def _point(text):
    if not isinstance(text, str) or len(text) > 48:
        raise PairingError('Invalid PAKE message.')
    value = base64.b64decode(text, validate=True)
    if len(value) != 33:
        raise PairingError('Invalid PAKE message length.')
    return value


def _certificate(text):
    if not isinstance(text, str) or len(text) > 4096 or 'PRIVATE KEY' in text:
        raise PairingError('Only a public certificate is accepted.')
    der = ssl.PEM_cert_to_DER_cert(text)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_verify_locations(cadata=ssl.DER_cert_to_PEM_cert(der))
    return der


def _keys(shared, transcript):
    keys = HKDF(algorithm=hashes.SHA256(), length=64,
                salt=hashlib.sha256(transcript).digest(), info=PROTOCOL).derive(shared)
    return keys[:32], keys[32:]


def _proof(key, label, *chunks):
    mac = hmac.new(key, PROTOCOL + label, hashlib.sha256)
    for chunk in chunks:
        mac.update(len(chunk).to_bytes(4, 'big') + chunk)
    return mac.hexdigest()


def _verify(message, expected):
    proof = message.get('proof')
    if not isinstance(proof, str) or not re.fullmatch(r'[0-9a-f]{64}', proof) or not hmac.compare_digest(proof, expected):
        raise PairingError('PIN or authenticated pairing data does not match.')


class PairingServer:
    def __init__(self, tls_dir, *, lifetime=PAIR_LIFETIME, max_attempts=MAX_ATTEMPTS):
        self.directory = Path(tls_dir)
        self.code = f'{secrets.randbelow(100_000_000):08d}'
        self.session = secrets.token_hex(16)
        self.expires = time.monotonic() + lifetime
        self.max_attempts = max_attempts
        self.attempts = 0
        self.paired = False
        self.finished = asyncio.Event()
        self._lock = asyncio.Lock()

    def _check_open(self):
        if self.paired or self.attempts >= self.max_attempts or time.monotonic() >= self.expires:
            raise PairingError('Pairing window is closed.')

    async def handle(self, ws):
        attempted = False
        try:
            async with asyncio.timeout(15):
                first = await _recv(ws)
                self._check_open()
                if first.get('type') == 'discover':
                    await _send(ws, 'server_info', protocol='sharemouse-python',
                                machine=socket.gethostname(), pairing=True)
                    return
                if first.get('type') != 'pair_info' or self._lock.locked():
                    raise PairingError('Pairing is busy.')
                async with self._lock:
                    self._check_open()
                    self.attempts += 1
                    attempted = True
                    await _send(ws, 'pair_info', session=self.session, version=1)
                    start = await _recv(ws, 'pair_start')
                    if start.get('session') != self.session:
                        raise PairingError('Stale pairing session.')
                    await self._exchange(ws, _point(start.get('pake')))
        except (PairingError, ValueError, TimeoutError, SPAKEError, link.WebSocketException, OSError):
            try:
                await asyncio.wait_for(_send(ws, 'error'), 0.5)
            except (TimeoutError, OSError, link.WebSocketException):
                pass
        finally:
            if self.paired or (attempted and self.attempts >= self.max_attempts):
                self.finished.set()

    async def _exchange(self, ws, a_message):
        pake = SPAKE2_B(self.code.encode('ascii'), idA=PROTOCOL + b'/client',
                       idB=PROTOCOL + b'/server/' + self.session.encode('ascii'))
        b_message = pake.start()
        shared = pake.finish(a_message)
        transcript = PROTOCOL + self.session.encode('ascii') + a_message + b_message
        client_key, server_key = _keys(shared, transcript)
        await _send(ws, 'pair_challenge', pake=base64.b64encode(b_message).decode('ascii'))
        confirmation = await _recv(ws, 'client_confirm')
        _verify(confirmation, _proof(client_key, b'/confirm', transcript))
        await _send(ws, 'server_confirm', proof=_proof(server_key, b'/confirm', transcript))

        # Both sides have confirmed the PAKE key before certificates are exchanged.
        message = await _recv(ws, 'client_cert')
        client_cert = _certificate(message.get('cert'))
        _verify(message, _proof(client_key, b'/certificate', transcript, client_cert))
        public = (self.directory / 'cert.pem').read_text(encoding='ascii')
        server_cert = _certificate(public)
        await _send(ws, 'server_cert', cert=public,
                    proof=_proof(server_key, b'/certificate', transcript, client_cert, server_cert))
        commit = await _recv(ws, 'pair_commit')
        _verify(commit, _proof(client_key, b'/commit', transcript, client_cert, server_cert))
        # The successful attempt is allowed to complete when its counter reaches max.
        if time.monotonic() >= self.expires or self.paired:
            raise PairingError('Pairing window expired.')
        await _send(ws, 'pair_done', proof=_proof(server_key, b'/done', transcript, client_cert, server_cert))
        saved = await _recv(ws, 'pair_saved')
        _verify(saved, _proof(client_key, b'/saved', transcript, client_cert, server_cert))
        if time.monotonic() >= self.expires:
            raise PairingError('Pairing window expired.')
        path, created = link.trust_certificate(self.directory, ssl.DER_cert_to_PEM_cert(client_cert))
        try:
            await _send(ws, 'pair_complete', proof=_proof(server_key, b'/complete', transcript, client_cert, server_cert))
        except BaseException:
            if created:
                path.unlink(missing_ok=True)
            raise
        self.paired = True
        self.finished.set()


async def pair_client(host, port, tls_dir, code):
    if not isinstance(code, str) or not re.fullmatch(r'[0-9]{8}', code):
        raise PairingError('Enter the eight digits shown on the server.')
    directory = Path(tls_dir)
    pending = None
    completed = False
    try:
        async with asyncio.timeout(15):
            address = f'[{host}]' if ':' in host else host
            async with link.connect(f'ws://{address}:{port}', open_timeout=3, max_size=8192) as ws:
                await _send(ws, 'pair_info')
                info = await _recv(ws, 'pair_info')
                session = info.get('session')
                if info.get('version') != 1 or not isinstance(session, str) or not re.fullmatch(r'[a-f0-9]{32}', session):
                    raise PairingError('Unsupported pairing session.')
                pake = SPAKE2_A(code.encode('ascii'), idA=PROTOCOL + b'/client',
                               idB=PROTOCOL + b'/server/' + session.encode('ascii'))
                a_message = pake.start()
                await _send(ws, 'pair_start', session=session, pake=base64.b64encode(a_message).decode('ascii'))
                challenge = await _recv(ws, 'pair_challenge')
                b_message = _point(challenge.get('pake'))
                shared = pake.finish(b_message)
                transcript = PROTOCOL + session.encode('ascii') + a_message + b_message
                client_key, server_key = _keys(shared, transcript)
                await _send(ws, 'client_confirm', proof=_proof(client_key, b'/confirm', transcript))
                _verify(await _recv(ws, 'server_confirm'), _proof(server_key, b'/confirm', transcript))
                public = (directory / 'cert.pem').read_text(encoding='ascii')
                client_cert = _certificate(public)
                await _send(ws, 'client_cert', cert=public,
                            proof=_proof(client_key, b'/certificate', transcript, client_cert))
                response = await _recv(ws, 'server_cert')
                server_cert = _certificate(response.get('cert'))
                _verify(response, _proof(server_key, b'/certificate', transcript, client_cert, server_cert))
                await _send(ws, 'pair_commit',
                            proof=_proof(client_key, b'/commit', transcript, client_cert, server_cert))
                _verify(await _recv(ws, 'pair_done'),
                        _proof(server_key, b'/done', transcript, client_cert, server_cert))
                pending = link.trust_certificate(directory, ssl.DER_cert_to_PEM_cert(server_cert))
                await _send(ws, 'pair_saved', proof=_proof(client_key, b'/saved', transcript, client_cert, server_cert))
                _verify(await _recv(ws, 'pair_complete'),
                        _proof(server_key, b'/complete', transcript, client_cert, server_cert))
                completed = True
    except (TimeoutError, SPAKEError, link.WebSocketException, ssl.SSLError) as error:
        raise PairingError('Pairing did not complete. Reopen pairing and verify the PIN.') from error
    finally:
        if not completed and pending is not None and pending[1]:
            pending[0].unlink(missing_ok=True)


async def open_server(host, port, tls_dir):
    session = PairingServer(tls_dir)
    async with link.serve(session.handle, host, port, max_size=8192):
        print(f'PAIRING PIN: {session.code}  (valid for {PAIR_LIFETIME} seconds, {MAX_ATTEMPTS} attempts)')
        print('On the client, select this server and enter the PIN. No file copying is needed.')
        try:
            await asyncio.wait_for(session.finished.wait(), PAIR_LIFETIME)
        except TimeoutError as error:
            raise PairingError('Pairing expired. Select Run again to create a new PIN.') from error
    if not session.paired:
        raise PairingError('Too many attempts. Open a new pairing window on the server.')
    print('Client paired. Switching to authenticated TLS.')
