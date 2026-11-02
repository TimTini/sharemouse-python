"""Fake-device regressions for high-rate motion, sleep and local escape. No input injection."""
import asyncio
import ctypes
import json
import threading
from unittest.mock import AsyncMock, Mock, PropertyMock, patch

import link
from client import MouseClient
from devices import Key, Swallow
from power import DISPLAY_STATE, DisplaySetting, PowerMonitor
from server import MouseServer


def make_server(peer):
    with patch('server.get_screen_size', return_value=(1920, 1080)):
        app = MouseServer()
    app.loop = asyncio.get_running_loop()
    app.running = app.control_active = True
    app.active_edge = 'right'
    app.clients[peer] = 'right'
    app._place = Mock()
    app._show_cursor = Mock()
    app.mouse_listener = Mock()
    app.keyboard_listener = Mock()
    return app


class Peer:
    remote_address = ('127.0.0.1', 12345)

    def __init__(self):
        self.sent = []
        self.close = AsyncMock()

    async def send(self, text):
        self.sent.append(json.loads(text))


async def check():
    peer = Peer()
    app = make_server(peer)
    for _ in range(1000):
        app._emit({'type': 'move', 'delta_x': 1, 'delta_y': -1})
    app._emit({'type': 'click', 'button': 'left', 'pressed': True})
    app._emit({'type': 'move', 'delta_x': 10, 'delta_y': 20})
    app._emit({'type': 'click', 'button': 'left', 'pressed': False})
    await asyncio.sleep(0.08)
    assert len(peer.sent) == 4, f'Burst sent {len(peer.sent)} messages instead of 4'
    assert len(peer.sent[0]['steps']) == 1000
    assert sum(step[0] for step in peer.sent[0]['steps']) == 1000
    assert sum(step[1] for step in peer.sent[0]['steps']) == -1000
    assert [p['type'] for p in peer.sent] == ['move', 'click', 'move', 'click']
    assert peer.sent[2]['delta_x'] == 10

    # Ordinary Escape still reaches the client; Ctrl+Alt+Esc rescues locally.
    app.on_press(Key.esc)
    app.on_release(Key.esc)
    assert app.control_active
    await asyncio.sleep(0.01)
    assert peer.sent[-1] == {'type': 'key_release', 'key': {'key': 'esc'}}
    app.keyboard_listener.suppress_event.side_effect = Swallow
    for key in (Key.ctrl_l, Key.alt_l, Key.esc):
        try:
            app.on_press(key)
        except Swallow:
            pass
    assert not app.control_active and app.active_edge is None
    app._show_cursor.assert_called_once()
    await asyncio.sleep(0.01)
    assert peer.sent[-1] == {'type': 'control', 'active': False}

    # A peer that accepts TCP writes but never acknowledges must lose ownership.
    peer = Peer()
    app = make_server(peer)
    with patch('server.HEARTBEAT_TIMEOUT', 0.03), patch('server.HEARTBEAT_INTERVAL', 0.01):
        await asyncio.wait_for(app._watch_client(peer), 0.3)
    assert not app.control_active
    peer.close.assert_awaited()

    # Native power state notification makes a client unavailable and releases input.
    with patch('client.get_screen_size', return_value=(800, 600)), \
            patch('client.MouseController'), patch('client.KeyController'):
        client = MouseClient()
    client.loop = asyncio.get_running_loop()
    client.websocket = Peer()
    client.control_active = True
    client.pressed_keys.add(Key.ctrl_l)
    client.on_power_change(False)
    await asyncio.sleep(0.02)
    assert not client.control_active and not client.available
    client.keys.release.assert_called_once_with(Key.ctrl_l)
    assert any(p['type'] == 'ready' and not p['ready'] for p in client.websocket.sent)
    await client.handle_control({'type': 'control', 'active': True})
    assert not client.control_active
    client.available = True
    await client.handle_control({'type': 'control', 'active': True})
    await client.handle_control({'type': 'control', 'active': False})
    assert not client.control_active

    # Replay the path but set the OS cursor only once. Net delta alone is wrong
    # after hitting an outer edge, or crossing back then changing direction.
    client.control_active = True
    client.x, client.y = 790, 100
    client.mouse.reset_mock()
    await client.handle_move({'steps': [[50, 0], [-50, 0]]})
    assert client.x == 749  # clamp at 799, then move 50 left (not net-zero 790).
    client.x, client.y = 0, 100
    await client.handle_move({'steps': [[-10, 1], [10, 1]]})
    assert not client.control_active and client.y == 101

    client.available = True
    with patch.object(type(client.mouse), 'position', new_callable=PropertyMock, create=True) as cursor:
        cursor.side_effect = OSError('desktop unavailable')
        try:
            await client.handle_control({'active': True})
        except OSError:
            pass
        else:
            raise AssertionError('Entry placement error was hidden')
    assert not client.control_active and not client.available
    assert client.websocket.sent[-1] == {'type': 'ready', 'ready': False}
    await client.handle_heartbeat({'id': 7})
    assert client.websocket.sent[-1] == {'type': 'heartbeat_ack', 'id': 7, 'ready': False}

    # Native callback decoding without changing monitor or sleep state.
    states = []
    monitor = PowerMonitor(states.append)
    setting = DisplaySetting(DISPLAY_STATE, 4, 0)
    monitor._notify(None, 0x8013, ctypes.byref(setting))
    monitor._notify(None, 4, None)
    setting.value = 1
    monitor._notify(None, 0x8013, ctypes.byref(setting))
    monitor._notify(None, 18, None)
    assert states == [False, False, False, True]

    await check_ready_peer()
    await check_silent_upgrade()
    await check_transition_race()
    print('motion batching and recovery: ok')


async def check_ready_peer():
    class ReadyPeer(Peer):
        def __init__(self):
            super().__init__()
            self.incoming = asyncio.Queue()
            self.ready = True

        async def send(self, text):
            await super().send(text)
            data = json.loads(text)
            if data['type'] == 'heartbeat':
                await self.incoming.put({'type': 'heartbeat_ack', 'id': data['id'], 'ready': self.ready})

        def __aiter__(self):
            return self.messages()

        async def messages(self):
            while True:
                data = await self.incoming.get()
                if data is None:
                    return
                yield json.dumps(data)

    peer = ReadyPeer()
    app = make_server(peer)
    await peer.incoming.put({'type': 'hello', 'position': 'right', 'ready': True})
    with patch('server.HEARTBEAT_TIMEOUT', 0.1), patch('server.HEARTBEAT_INTERVAL', 0.01):
        task = asyncio.create_task(app.handle_client(peer))
        await asyncio.sleep(0.04)
        assert app.control_active and any(p['type'] == 'heartbeat' for p in peer.sent)
        peer.ready = False
        await peer.incoming.put({'type': 'ready', 'ready': False})
        await asyncio.sleep(0.005)
        assert not app.control_active and app._available[peer] is False
        await peer.incoming.put(None)
        await asyncio.wait_for(task, 0.2)
    assert not app.clients and not app._heartbeats


async def check_silent_upgrade():
    release = asyncio.Event()
    sessions = []

    async def silent(reader, writer):
        sessions.append(asyncio.current_task())
        try:
            await reader.read(4096)
            await release.wait()
        finally:
            writer.close()

    async with await asyncio.start_server(silent, '127.0.0.1', 0) as server:
        port = server.sockets[0].getsockname()[1]
        try:
            async with asyncio.timeout(0.3):
                try:
                    async with link.connect(f'ws://127.0.0.1:{port}', open_timeout=0.03):
                        raise AssertionError('Silent upgrade accepted')
                except TimeoutError:
                    pass
        finally:
            release.set()
            await asyncio.gather(*sessions)

    writer = Mock()
    writer.drain = AsyncMock(side_effect=asyncio.Event().wait)
    socket = link.Socket(Mock(), writer, ('127.0.0.1', 0), None, False)
    try:
        await asyncio.wait_for(socket.close(), 0.02)
    except TimeoutError:
        pass
    writer.close.assert_called_once()


async def check_transition_race():
    peer = Peer()
    app = make_server(peer)
    app.control_active = False
    app.active_edge = None
    started, finish = threading.Event(), threading.Event()

    def capture():
        started.set()
        assert finish.wait(1)
        app._place(app.park_x, app.park_y)

    app._capture = capture
    activation = threading.Thread(target=app._activate, args=('right', 1919, 500))
    activation.start()
    assert await asyncio.to_thread(started.wait, 0.5)
    reclaim = threading.Thread(target=app._set_ready, args=(peer, False))
    reclaim.start()
    await asyncio.sleep(0.01)
    finish.set()
    await asyncio.to_thread(activation.join, 0.5)
    await asyncio.to_thread(reclaim.join, 0.5)
    assert not activation.is_alive() and not reclaim.is_alive()
    assert not app.control_active
    assert app._place.call_args.args == (1914, 540), 'Late capture overwrote the local return position'


if __name__ == '__main__':
    asyncio.run(check())
