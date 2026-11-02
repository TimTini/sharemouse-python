"""Mixed-resolution handoff checks using real handlers and fake OS devices."""
import asyncio
from fractions import Fraction
import json
import logging
from unittest.mock import Mock, patch

from client import MouseClient
from server import MouseServer
from utils import EDGE_THRESHOLD, POSITIONS, entry_point, hit_edge, normalize_position, return_point

RESOLUTIONS = ((640, 480), (1366, 768), (1920, 1080), (2560, 1440),
               (3840, 2160), (5120, 2880), (1080, 1920), (3440, 1440))
INPUT_TYPES = {'control', 'move', 'click', 'scroll', 'key_press', 'key_release'}


def edge_point(edge, size, tangent):
    width, height = size
    if edge == 'right':
        return width - 1, tangent
    if edge == 'left':
        return 0, tangent
    if edge == 'top':
        return tangent, 0
    return tangent, height - 1


def geometry_matrix():
    count = 0
    for primary in RESOLUTIONS:
        for secondary in RESOLUTIONS:
            for edge in POSITIONS:
                axis = 1 if edge in ('left', 'right') else 0
                a, b = primary[axis], secondary[axis]
                for tangent in (0, 1, a // 7, a // 2, a - 2, a - 1):
                    source = edge_point(edge, primary, tangent)
                    x, y = normalize_position(*source, *primary)
                    target = entry_point(edge, x, y, *secondary)
                    # Rational reference, allowing one pixel for float rounding.
                    assert abs(target[axis] - int(Fraction(tangent * b, a))) <= 1
                    assert 0 <= target[0] < secondary[0] and 0 <= target[1] < secondary[1]
                    if edge == 'right':
                        assert target[0] == 0
                    elif edge == 'left':
                        assert target[0] == secondary[0] - 1
                    elif edge == 'top':
                        assert target[1] == secondary[1] - 1
                    else:
                        assert target[1] == 0
                    back = return_point(edge, target[0] / secondary[0], target[1] / secondary[1], *primary)
                    assert abs(back[axis] - tangent) <= (a + b - 1) // b + 1
                    assert hit_edge(*back, *primary, {edge}) is None
                    count += 1
    return count


async def settle(predicate):
    async with asyncio.timeout(1):
        while not predicate():
            await asyncio.sleep(0.001)


class Peer:
    remote_address = ('127.0.0.1', 12345)

    def __init__(self, client):
        self.client = client
        self.incoming = asyncio.Queue()
        self.sent = []
        client.websocket = Mock(send=self.from_client)

    async def from_client(self, text):
        await self.incoming.put(text)

    async def send(self, text):
        self.sent.append(json.loads(text))
        await self.client.handle_message(text)

    async def close(self):
        await self.incoming.put(None)

    def __aiter__(self):
        return self.messages()

    async def messages(self):
        while True:
            message = await self.incoming.get()
            if message is None:
                return
            yield message

    def input_count(self):
        return sum(p['type'] in INPUT_TYPES for p in self.sent)


async def layout(primary, dimensions, fractions=(0.37,)):
    with patch('server.get_screen_size', return_value=primary):
        server = MouseServer()
    server.loop = asyncio.get_running_loop()
    server.running = True
    server._capture = Mock()
    server._place = Mock()
    server._show_cursor = Mock()
    peers, tasks = {}, []
    try:
        for edge, size in dimensions.items():
            with patch('client.get_screen_size', return_value=size), \
                    patch('client.MouseController'), patch('client.KeyController'):
                client = MouseClient(position=edge)
            peer = peers[edge] = Peer(client)
            tasks.append(asyncio.create_task(server.handle_client(peer)))
            await peer.incoming.put(json.dumps({'type': 'hello', 'position': edge, 'ready': True}))
        await settle(lambda: len(server.clients) == len(peers))
        for fraction in fractions:
            for edge, peer in peers.items():
                client = peer.client
                axis = 1 if edge in ('left', 'right') else 0
                before = {other: other.input_count() for other in peers.values()}
                source = edge_point(edge, primary, int(primary[axis] * fraction))
                server._on_move(*source)
                await settle(lambda: client.control_active)
                assert server.active_edge == edge
                expected_entry = entry_point(edge, *normalize_position(*source, *primary), *dimensions[edge])
                assert (client.x, client.y) == expected_entry

                # Relative motion remains in client pixels, independently of resolution.
                dx, dy = (0, 17) if axis == 1 else (17, 0)
                server._on_move(server.park_x + dx, server.park_y + dy)
                await settle(lambda: not server._sending)
                assert (client.x, client.y) == (expected_entry[0] + dx, expected_entry[1] + dy)
                assert server._place.call_args.args == (server.park_x, server.park_y)

                width, height = dimensions[edge]
                steps = {
                    'right': [(width * 2, height), (-width // 4, -height // 3)],
                    'left': [(-width * 2, height), (width // 4, -height // 3)],
                    'top': [(width, -height * 2), (-width // 3, height // 4)],
                    'bottom': [(width, height * 2), (-width // 3, -height // 4)],
                }[edge]
                expected = [client.x, client.y]
                for dx, dy in steps:
                    expected = [min(max(expected[0] + dx, 0), width - 1),
                                min(max(expected[1] + dy, 0), height - 1)]
                    server._emit({'type': 'move', 'delta_x': dx, 'delta_y': dy})
                await settle(lambda: not server._sending)
                assert (client.x, client.y) == tuple(expected), (primary, dimensions[edge], edge)

                # Scroll/click only reach this client in a mixed layout.
                server._emit({'type': 'click', 'button': 'left', 'pressed': True})
                server._emit({'type': 'click', 'button': 'left', 'pressed': False})
                server._emit({'type': 'scroll', 'dx': 0, 'dy': 1})
                await settle(lambda: not server._sending)
                client.mouse.press.assert_called_once()
                client.mouse.scroll.assert_called_once_with(0, 1)
                client.mouse.reset_mock()

                exits = {'right': (-EDGE_THRESHOLD - 1, client.y),
                         'left': (width + EDGE_THRESHOLD, client.y),
                         'top': (client.x, height + EDGE_THRESHOLD),
                         'bottom': (client.x, -EDGE_THRESHOLD - 1)}
                x, y = exits[edge]
                server._emit({'type': 'move', 'delta_x': x - client.x, 'delta_y': y - client.y})
                await settle(lambda: not server.control_active and not server._sending)
                assert not client.control_active
                local = server._place.call_args.args
                tangent = min(max((x, y)[axis], 0), dimensions[edge][axis] - 1)
                expected_tangent = int(Fraction(tangent * primary[axis], dimensions[edge][axis]))
                assert abs(local[axis] - expected_tangent) <= 1
                assert hit_edge(*local, *primary, set(peers)) is None
                assert all(other.input_count() == before[other] for other in peers.values() if other is not peer)
    finally:
        server.running = False
        for peer in peers.values():
            await peer.close()
        await asyncio.gather(*tasks)


async def check():
    count = geometry_matrix()
    transfers = 0
    for primary in RESOLUTIONS:
        for secondary in RESOLUTIONS:
            for edge in POSITIONS:
                await layout(primary, {edge: secondary})
                transfers += 1
    for index, primary in enumerate(((1920, 1080), (3840, 2160), (1366, 768))):
        sizes = [(640, 480), (5120, 2880), (1080, 1920), (3440, 1440)]
        sizes = sizes[index:] + sizes[:index]
        await layout(primary, dict(zip(POSITIONS, sizes)), fractions=(0.37, 0.8))
    print(f'resolutions: {count} geometry checks, {transfers} real-handler round trips, '
          '3 mixed layouts / 24 transfers: ok (fake OS devices)')


if __name__ == '__main__':
    logging.getLogger('server').setLevel(logging.WARNING)
    logging.getLogger('client').setLevel(logging.WARNING)
    asyncio.run(check())
