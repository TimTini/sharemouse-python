"""Real loopback PAKE bootstrap; temporary identities, no input devices."""
import asyncio
import json
from pathlib import Path
import tempfile
from unittest.mock import patch

import link
from tests import ROOT


async def check():
    assert (ROOT / 'pairing.py').exists(), 'No file-free pairing implementation'
    import pairing
    scratch = ROOT / '.cache/tests'
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=scratch) as folder:
        root = Path(folder)
        server_dir, client_dir, other_dir = (root / name for name in ('server', 'client', 'other'))
        for directory in (server_dir, client_dir, other_dir):
            link.ensure_identity(directory)
        keys = {directory: (directory / 'key.pem').read_bytes() for directory in (server_dir, client_dir)}

        # Read-only discovery must work before any peer certificate is trusted.
        session = pairing.PairingServer(server_dir)
        async with link.serve(session.handle, '127.0.0.1', 0, max_size=8192) as listener:
            port = listener.sockets[0].getsockname()[1]
            from configure import probe_server
            for _ in range(100):
                found = await probe_server('127.0.0.1', port, client_dir)
                assert found and found['pairing'] is True
            assert not link.peer_certs(client_dir) and not link.peer_certs(server_dir)

            async with link.connect(f'ws://127.0.0.1:{port}', max_size=8192) as ws:
                await pairing._send(ws, 'move', dx=10, dy=10)
                assert json.loads(await ws.recv()) == {'type': 'error'}
            assert session.attempts == 0

            bad = session.code[:-1] + str((int(session.code[-1]) + 1) % 10)
            try:
                await pairing.pair_client('127.0.0.1', port, client_dir, bad)
            except pairing.PairingError:
                pass
            else:
                raise AssertionError('Wrong PIN accepted')
            assert not link.peer_certs(client_dir) and not link.peer_certs(server_dir)

            frames = []
            original = pairing._send

            async def record(ws, kind, **fields):
                frames.append(json.dumps({'type': kind, **fields}))
                return await original(ws, kind, **fields)

            with patch('pairing._send', side_effect=record):
                await pairing.pair_client('127.0.0.1', port, client_dir, session.code)
            assert session.paired and len(link.peer_certs(server_dir)) == len(link.peer_certs(client_dir)) == 1
            assert session.code not in ''.join(frames) and 'PRIVATE KEY' not in ''.join(frames)
            for directory, key in keys.items():
                assert (directory / 'key.pem').read_bytes() == key
            try:
                await pairing.pair_client('127.0.0.1', port, other_dir, session.code)
            except pairing.PairingError:
                pass
            else:
                raise AssertionError('Used pairing window accepted another client')

        # Pairing results must actually authenticate mTLS on the normal transport.
        async def echo(ws):
            await ws.send(await ws.recv())
        async with link.serve(echo, '127.0.0.1', 0, ssl=link.server_context(server_dir)) as listener:
            port = listener.sockets[0].getsockname()[1]
            async with link.connect(f'wss://127.0.0.1:{port}', ssl=link.client_context(client_dir)) as ws:
                await ws.send('paired')
                assert await ws.recv() == 'paired'

        # Exercise the actual menu entrypoint: it must keep listening until paired.
        ready = asyncio.Event()
        listeners = []
        serve = link.serve
        session = pairing.PairingServer(server_dir)

        def listen(*args, **kwargs):
            listener = serve(*args, **kwargs)
            listeners.append(listener)
            return listener

        with patch('pairing.PairingServer', return_value=session), \
                patch('link.serve', side_effect=listen), \
                patch('builtins.print', side_effect=lambda *args: ready.set()):
            task = asyncio.create_task(pairing.open_server('127.0.0.1', 0, server_dir))
            try:
                await asyncio.wait_for(ready.wait(), 2)
                assert not task.done()
                await pairing.pair_client('127.0.0.1', listeners[0].sockets[0].getsockname()[1], client_dir, session.code)
                await asyncio.wait_for(task, 2)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        assert session.paired
        with patch('pairing.PAIR_LIFETIME', 0), patch('builtins.print'):
            try:
                await pairing.open_server('127.0.0.1', 0, server_dir)
            except pairing.PairingError as error:
                assert 'expired' in str(error)
            else:
                raise AssertionError('Expired menu pairing reported success')

        # Tampered authenticated certificate exchange must not create trust.
        a, b = root / 'tamper-server', root / 'tamper-client'
        link.ensure_identity(a)
        link.ensure_identity(b)
        session = pairing.PairingServer(a)

        async def tamper(ws, kind, **fields):
            if kind == 'client_cert':
                fields['proof'] = '0' * 64
            return await original(ws, kind, **fields)

        async with link.serve(session.handle, '127.0.0.1', 0, max_size=8192) as listener:
            with patch('pairing._send', side_effect=tamper):
                try:
                    await pairing.pair_client('127.0.0.1', listener.sockets[0].getsockname()[1], b, session.code)
                except pairing.PairingError:
                    pass
                else:
                    raise AssertionError('Modified certificate proof accepted')
        assert not link.peer_certs(a) and not link.peer_certs(b)

        # Replay a confirmation under a new session: no new trust is created.
        session = pairing.PairingServer(a)
        old_start = next(json.loads(f) for f in frames if json.loads(f)['type'] == 'pair_start')
        old_confirm = next(json.loads(f) for f in frames if json.loads(f)['type'] == 'client_confirm')
        async with link.serve(session.handle, '127.0.0.1', 0, max_size=8192) as listener:
            port = listener.sockets[0].getsockname()[1]
            async with link.connect(f'ws://127.0.0.1:{port}', max_size=8192) as ws:
                await original(ws, 'pair_info')
                info = await pairing._recv(ws, 'pair_info')
                await original(ws, 'pair_start', session=info['session'], pake=old_start['pake'])
                await pairing._recv(ws, 'pair_challenge')
                await original(ws, 'client_confirm', proof=old_confirm['proof'])
                try:
                    await pairing._recv(ws, 'server_confirm')
                except pairing.PairingError:
                    pass
                else:
                    raise AssertionError('Replayed confirmation accepted')
        assert not link.peer_certs(a) and not link.peer_certs(b)

        # Losing either final delivery must roll back only newly created trust.
        for dropped in ('pair_done', 'pair_complete'):
            session = pairing.PairingServer(a)

            async def drop(ws, kind, **fields):
                if kind == dropped:
                    raise link.ConnectionClosed()
                return await original(ws, kind, **fields)

            async with link.serve(session.handle, '127.0.0.1', 0, max_size=8192) as listener:
                with patch('pairing._send', side_effect=drop):
                    try:
                        await pairing.pair_client('127.0.0.1', listener.sockets[0].getsockname()[1], b, session.code)
                    except pairing.PairingError:
                        pass
                    else:
                        raise AssertionError('Pairing reported success without final confirmation')
            assert not session.paired and not link.peer_certs(a) and not link.peer_certs(b)

        # A successful send cannot prove final delivery. Re-pairing repairs
        # retained server trust without creating duplicate certificates.
        session = pairing.PairingServer(a)
        pairing_recv = pairing._recv

        async def lose_delivery(ws, expected=None):
            response = await pairing_recv(ws, expected)
            if expected == 'pair_complete':
                raise link.ConnectionClosed()
            return response

        async with link.serve(session.handle, '127.0.0.1', 0, max_size=8192) as listener:
            with patch('pairing._recv', side_effect=lose_delivery):
                try:
                    await pairing.pair_client('127.0.0.1', listener.sockets[0].getsockname()[1], b, session.code)
                except pairing.PairingError:
                    pass
                else:
                    raise AssertionError('Pairing succeeded after losing final delivery')
        assert session.paired and len(link.peer_certs(a)) == 1 and not link.peer_certs(b)
        session = pairing.PairingServer(a)
        async with link.serve(session.handle, '127.0.0.1', 0, max_size=8192) as listener:
            await pairing.pair_client('127.0.0.1', listener.sockets[0].getsockname()[1], b, session.code)
        assert len(link.peer_certs(a)) == len(link.peer_certs(b)) == 1
        for directory in (a, b):
            for certificate in link.peer_certs(directory):
                certificate.unlink()

        # Attempt budget and expiry close the window without trusting a peer.
        session = pairing.PairingServer(a, max_attempts=1)
        async with link.serve(session.handle, '127.0.0.1', 0, max_size=8192) as listener:
            for pin in ('not-a-pin', session.code[:-1] + str((int(session.code[-1]) + 1) % 10), session.code):
                try:
                    await pairing.pair_client('127.0.0.1', listener.sockets[0].getsockname()[1], b, pin)
                except pairing.PairingError:
                    pass
                else:
                    raise AssertionError('Invalid/locked attempt accepted')
        assert session.attempts == 1 and not session.paired
        session = pairing.PairingServer(a, lifetime=0)
        async with link.serve(session.handle, '127.0.0.1', 0, max_size=8192) as listener:
            try:
                await pairing.pair_client('127.0.0.1', listener.sockets[0].getsockname()[1], b, session.code)
            except pairing.PairingError:
                pass
            else:
                raise AssertionError('Expired pairing accepted')
        assert not link.peer_certs(a) and not link.peer_certs(b)
    print('PAKE pairing, fresh discovery, tamper/expiry/lockout, mTLS and local-only private keys: ok')


if __name__ == '__main__':
    link.run(check())
