"""Regression: returning/disconnecting must release remote input, using fake devices."""
import asyncio
import json
from unittest.mock import AsyncMock, Mock, patch

from devices import Button, Key, Swallow

from client import MouseClient
from server import MouseServer


async def check():
    with patch('client.get_screen_size', return_value=(800, 600)), \
            patch('client.MouseController'), patch('client.KeyController'):
        app = MouseClient()
    app.control_active = True
    app.websocket = Mock()
    app.websocket.send = AsyncMock()
    await app.handle_key_press({'key': {'key': 'ctrl_l'}})
    await app.handle_click({'button': 'left', 'pressed': True})
    await app.return_control()
    app.keys.release.assert_called_once_with(Key.ctrl_l)
    app.mouse.release.assert_called_once_with(Button.left)
    app.stop()
    assert app.keys.release.call_count == 1  # cleanup is idempotent

    app.control_active = True
    await app.handle_key_press({'key': {'key': 'shift'}})
    await app.handle_click({'button': 'right', 'pressed': True})
    app.stop()
    assert app.keys.release.call_args.args == (Key.shift,)
    assert app.mouse.release.call_args.args == (Button.right,)

    # Simulate a connection ending after remote input is pressed, without sockets.
    class Connection:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def send(self, message):
            assert json.loads(message)['type'] == 'hello'

        def __aiter__(self):
            return self.messages()

        async def messages(self):
            yield json.dumps({'type': 'control', 'active': True})
            yield json.dumps({'type': 'key_press', 'key': {'key': 'alt_l'}})
            yield json.dumps({'type': 'click', 'button': 'middle', 'pressed': True})
            app.running = False

    with patch('client.link.connect', return_value=Connection()):
        await app.connect()
    assert app.keys.release.call_args.args == (Key.alt_l,)
    assert app.mouse.release.call_args.args == (Button.middle,)
    assert not app.control_active

    class CancelledConnection(Connection):
        async def messages(self):
            yield json.dumps({'type': 'control', 'active': True})
            yield json.dumps({'type': 'key_press', 'key': {'key': 'ctrl_l'}})
            raise asyncio.CancelledError()

    releases = app.keys.release.call_count
    with patch('client.link.connect', return_value=CancelledConnection()):
        try:
            await app.connect()
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError('Cancellation was swallowed')
    assert app.keys.release.call_count == releases + 1
    assert app.websocket is None and not app.control_active

    # A hello message exposes the actual peer's label/address in the server log.
    with patch('server.get_screen_size', return_value=(800, 600)):
        server = MouseServer()

    class Peer:
        remote_address = ('192.168.1.10', 12345)

        def __aiter__(self):
            return self.messages()

        async def messages(self):
            yield json.dumps({'type': 'hello', 'position': 'left', 'machine': 'CLIENT-PC'})
            assert server.clients[self] == 'left'

    peer = Peer()
    with patch('server.logger.info') as info:
        await server.handle_client(peer)
        info.assert_any_call('client %r at %s on %s (%d connected)',
                             'CLIENT-PC', peer.remote_address, 'left', 1)
    assert not server.clients

    # Captured moves must be swallowed. Otherwise the cursor stays on the edge
    # and every delta is "edge - center" (2K center is 1280; left edge -> about -1270).
    with patch('server.get_screen_size', return_value=(2560, 1440)):
        server = MouseServer()
    server.running = True
    server.control_active = True
    server.mouse_listener = Mock()
    server.mouse_listener.suppress_event.side_effect = Swallow
    emitted = []
    server._emit = emitted.append
    server._place = Mock()
    try:
        server.on_move(10, 100)
    except Swallow:
        pass
    else:
        raise AssertionError('captured move was not swallowed')
    assert emitted == [{'type': 'move', 'delta_x': 10 - 1280, 'delta_y': 100 - 720}]
    server._place.assert_called_once_with(1280, 720)

    server.control_active = False
    server.on_move(400, 400)
    assert server.mouse_listener.suppress_event.call_count == 1


if __name__ == '__main__':
    asyncio.run(check())
    print('input cleanup: ok')
