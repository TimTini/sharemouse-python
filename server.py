"""Primary PC. Watches the pointer and hands it to a peer across a screen edge."""
import asyncio
from collections import deque
import ctypes
import json
import logging
import math
import platform
import socket
import threading

import link
from devices import Key, KeyListener, MouseListener

from utils import (
    encode_key,
    get_screen_size,
    hit_edge,
    normalize_position,
    place_cursor,
    return_point,
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
HEARTBEAT_INTERVAL = 0.5
HEARTBEAT_TIMEOUT = 2.0


class MouseServer:
    def __init__(self, host='0.0.0.0', port=8765, edge_threshold=5, tls_dir=None, move_hz=240):
        if not 30 <= move_hz <= 1000:
            raise ValueError('move_hz must be between 30 and 1000')
        self.host = host
        self.port = port
        self.tls_dir = tls_dir
        self.edge_threshold = edge_threshold
        self.screen_width, self.screen_height = get_screen_size()
        self.clients = {}
        self.running = False
        self.control_active = False
        self.active_edge = None
        self.loop = None
        self._lock = threading.Lock()
        self._transition_lock = threading.RLock()
        self._placing = False
        self._cursor_hidden = False
        self.park_x = self.screen_width // 2
        self.park_y = self.screen_height // 2
        self._move_interval = 1 / move_hz
        self._last_move_at = 0
        self._outbox = deque()
        self._sending = False
        self._sender_task = None
        self._available = {}
        self._heartbeats = {}
        self._escape_held = False
        self._held_keys = set()

    async def _send(self, targets, message):
        for ws in targets:
            try:
                await asyncio.wait_for(ws.send(message), HEARTBEAT_TIMEOUT)
            except (OSError, TimeoutError, link.WebSocketException):
                logger.warning('client send failed; reclaiming local control')
                await self._disconnect_peer(ws)

    def _emit(self, payload):
        with self._lock:
            edge = self.active_edge
            targets = [ws for ws, pos in self.clients.items() if pos == edge]
            self._enqueue(targets, payload)

    def _enqueue(self, targets, payload):
        """Caller holds _lock, so events cannot cross an ownership transition."""
        if not targets or self.loop is None or self.loop.is_closed():
            return
        targets = tuple(targets)
        if (payload['type'] == 'move' and self._outbox
                and self._outbox[-1][0] == targets and self._outbox[-1][1]['type'] == 'move'):
            pending = self._outbox[-1][1]
            if 'steps' not in pending:
                pending['steps'] = [[pending.pop('delta_x'), pending.pop('delta_y')]]
            pending['steps'].append([payload['delta_x'], payload['delta_y']])
        else:
            self._outbox.append((targets, dict(payload)))
        if not self._sending:
            self._sending = True
            self.loop.call_soon_threadsafe(self._start_sender)

    def _start_sender(self):
        self._sender_task = asyncio.create_task(self._drain_outbox())

    async def _drain_outbox(self):
        try:
            while True:
                with self._lock:
                    if not self._outbox:
                        return
                    moving = self._outbox[0][1]['type'] == 'move'
                if moving:
                    await asyncio.sleep(max(0, self._move_interval - (self.loop.time() - self._last_move_at)))
                with self._lock:
                    if not self._outbox:
                        continue
                    targets, payload = self._outbox.popleft()
                await self._send(targets, json.dumps(payload, separators=(',', ':')))
                if payload['type'] == 'move':
                    self._last_move_at = self.loop.time()
        finally:
            with self._lock:
                self._sending = False
                if self._outbox and self.running and not self.loop.is_closed():
                    self._sending = True
                    self.loop.call_soon(self._start_sender)

    def _place(self, x, y):
        self._placing = True
        try:
            place_cursor(x, y)
        finally:
            self._placing = False

    def _hide_cursor(self):
        # ponytail: cursor hide and key/click swallow are Windows-only
        if self._cursor_hidden or platform.system() != 'Windows':
            return
        user32 = ctypes.windll.user32
        while user32.ShowCursor(False) >= 0:
            pass
        self._cursor_hidden = True

    def _show_cursor(self):
        if not self._cursor_hidden:
            return
        user32 = ctypes.windll.user32
        while user32.ShowCursor(True) < 0:
            pass
        self._cursor_hidden = False

    def _capture(self):
        self._hide_cursor()
        self._place(self.park_x, self.park_y)

    def _end_control(self, exit_x, exit_y):
        # Serialize cursor side effects as well as ownership. A late capture
        # must not park the pointer after a concurrent reclaim has returned it.
        with self._transition_lock:
            with self._lock:
                if not self.control_active:
                    return
                edge = self.active_edge
                targets = [ws for ws, pos in self.clients.items() if pos == edge]
                self.control_active = False
                self.active_edge = None
                self._outbox.clear()
                self._enqueue(targets, {'type': 'control', 'active': False})
            try:
                exit_x, exit_y = float(exit_x), float(exit_y)
                if not math.isfinite(exit_x) or not math.isfinite(exit_y):
                    exit_x, exit_y = 0.5, 0.5
            except (TypeError, ValueError):
                exit_x, exit_y = 0.5, 0.5
            x, y = return_point(
                edge, exit_x, exit_y, self.screen_width, self.screen_height, self.edge_threshold
            )
            try:
                self._place(x, y)
            finally:
                self._show_cursor()
        logger.info('control returned at (%s, %s)', x, y)

    def _activate(self, edge, x, y):
        entry_x, entry_y = normalize_position(x, y, self.screen_width, self.screen_height)
        with self._transition_lock:
            with self._lock:
                if self.control_active or not self.running or not any(
                    pos == edge and self._available.get(ws, True) for ws, pos in self.clients.items()
                ):
                    return
                self.control_active = True
                self.active_edge = edge
            try:
                self._capture()
            except OSError:
                self._end_control(0.5, 0.5)
                raise
            with self._lock:
                self._enqueue([ws for ws, pos in self.clients.items() if pos == edge], {
                    'type': 'control', 'active': True, 'entry_x': entry_x, 'entry_y': entry_y,
                })
        logger.info('control -> %s', edge)

    def on_move(self, x, y, injected=False):
        if not self.running or self._placing or injected:
            return
        try:
            self._on_move(x, y)
        except Exception:
            logger.exception('move')
        # Keep the local cursor parked. If this move is delivered, Windows puts
        # it back on the physical spot (the screen edge). The next delta is then
        # "edge minus center", and the other PC's pointer sticks to the far side.
        if self.control_active:
            self._swallow_mouse()

    def _on_move(self, x, y):
        if not self.control_active:
            with self._lock:
                edges = {pos for ws, pos in self.clients.items() if self._available.get(ws, True)}
            edge = hit_edge(
                x, y, self.screen_width, self.screen_height, edges, self.edge_threshold
            )
            if edge:
                self._activate(edge, x, y)
            return
        dx = x - self.park_x
        dy = y - self.park_y
        if dx or dy:
            self._emit({'type': 'move', 'delta_x': dx, 'delta_y': dy})
            self._place(self.park_x, self.park_y)

    def _swallow_mouse(self):
        fn = getattr(self.mouse_listener, 'suppress_event', None)
        if fn and self.control_active:
            fn()

    def on_click(self, x, y, button, pressed, injected=False):
        if injected or not self.running or not self.control_active:
            return
        try:
            self._emit({'type': 'click', 'button': button.name, 'pressed': pressed})
        finally:
            self._swallow_mouse()

    def on_scroll(self, x, y, dx, dy, injected=False):
        if injected or not self.running or not self.control_active:
            return
        try:
            self._emit({'type': 'scroll', 'dx': dx, 'dy': dy})
        finally:
            self._swallow_mouse()

    def _emit_key(self, kind, key):
        encoded = encode_key(key)
        if encoded is not None:
            self._emit({'type': kind, 'key': encoded})

    def _on_key(self, pressed, key, injected):
        if injected:
            return
        if key is not None:
            if pressed:
                self._held_keys.add(key)
            else:
                self._held_keys.discard(key)
        rescue = (self.control_active
                  and self._held_keys.intersection((Key.ctrl_l, Key.ctrl_r))
                  and self._held_keys.intersection((Key.alt_l, Key.alt_r)))
        if key == Key.esc and (rescue or self._escape_held):
            self._escape_held = pressed
            if pressed:
                self._end_control(0.5, 0.5)
            self.keyboard_listener.suppress_event()
            return
        if not self.control_active:
            return
        try:
            if key is not None:
                kind = 'key_press' if pressed else 'key_release'
                self._emit_key(kind, key)
        finally:
            self.keyboard_listener.suppress_event()

    def on_press(self, key, injected=False):
        self._on_key(True, key, injected)

    def on_release(self, key, injected=False):
        self._on_key(False, key, injected)

    def _set_ready(self, websocket, ready):
        with self._lock:
            self._available[websocket] = ready
            owns = self.control_active and self.clients.get(websocket) == self.active_edge
        if not ready and owns:
            self._end_control(0.5, 0.5)

    async def _disconnect_peer(self, websocket):
        with self._lock:
            pos = self.clients.pop(websocket, None)
            self._available.pop(websocket, None)
            owns = self.control_active and pos == self.active_edge
        if owns:
            self._end_control(0.5, 0.5)
        try:
            await asyncio.wait_for(websocket.close(), 0.5)
        except (OSError, TimeoutError, link.WebSocketException):
            logger.warning('client close did not complete')

    async def _watch_client(self, websocket):
        event = asyncio.Event()
        token = 0
        try:
            while websocket in self.clients:
                token += 1
                event.clear()
                self._heartbeats[websocket] = (token, event)
                async with asyncio.timeout(HEARTBEAT_TIMEOUT):
                    await websocket.send(json.dumps({'type': 'heartbeat', 'id': token}))
                    await event.wait()
                await asyncio.sleep(HEARTBEAT_INTERVAL)
        except (OSError, TimeoutError, link.WebSocketException):
            logger.warning('client heartbeat lost; reclaiming local control')
            await self._disconnect_peer(websocket)
        finally:
            self._heartbeats.pop(websocket, None)

    async def handle_client(self, websocket):
        watcher = None
        try:
            async for message in websocket:
                try:
                    data = json.loads(message)
                except json.JSONDecodeError:
                    continue
                if not isinstance(data, dict):
                    continue
                kind = data.get('type')
                if kind == 'discover':
                    await websocket.send(json.dumps({
                        'type': 'server_info', 'protocol': 'sharemouse-python',
                        'machine': socket.gethostname(),
                    }))
                    return
                elif kind == 'hello':
                    pos = data.get('position')
                    if pos not in ('left', 'right', 'top', 'bottom'):
                        logger.warning('ignored client position %r', pos)
                        continue
                    with self._lock:
                        replaced = [other for other, side in self.clients.items()
                                    if other is not websocket and side == pos]
                    for other in replaced:
                        await self._disconnect_peer(other)
                    with self._lock:
                        self.clients[websocket] = pos
                        count = len(self.clients)
                    self._set_ready(websocket, data.get('ready', True) is True)
                    if watcher is None:
                        watcher = asyncio.create_task(self._watch_client(websocket))
                    logger.info('client %r at %s on %s (%d connected)',
                                data.get('machine', 'unknown'), websocket.remote_address, pos, count)
                elif kind in ('ready', 'heartbeat_ack') and websocket in self.clients:
                    expected = self._heartbeats.get(websocket)
                    if kind == 'ready' or (expected and data.get('id') == expected[0]):
                        self._set_ready(websocket, data.get('ready') is True)
                        if kind == 'heartbeat_ack':
                            expected[1].set()
                elif kind == 'return_control':
                    with self._lock:
                        owns = (
                            self.control_active
                            and self.clients.get(websocket) == self.active_edge
                        )
                    if owns:
                        self._end_control(data.get('exit_x', 0.5), data.get('exit_y', 0.5))
        except link.ConnectionClosed:
            pass
        finally:
            if watcher is not None:
                watcher.cancel()
                await asyncio.gather(watcher, return_exceptions=True)
            with self._lock:
                self.clients.pop(websocket, None)
                self._available.pop(websocket, None)
                orphan = self.control_active and not any(
                    pos == self.active_edge for pos in self.clients.values()
                )
                count = len(self.clients)
            if orphan:
                self._end_control(0.5, 0.5)
            logger.info('client left (%d connected)', count)

    async def start(self):
        if self.tls_dir is None:
            raise ValueError('TLS certificate directory is missing.')
        self.tls_dir = link.resolve_tls_dir(self.tls_dir)
        ssl_context = link.server_context(self.tls_dir)
        peers = link.peer_certs(self.tls_dir)
        if not peers:
            raise ValueError('No paired client. Open run_sharemouse.cmd and pair using its PIN menu.')
        self.running = True
        self.loop = asyncio.get_running_loop()
        self.mouse_listener = MouseListener(
            on_move=self.on_move,
            on_click=self.on_click,
            on_scroll=self.on_scroll,
        )
        self.keyboard_listener = KeyListener(
            on_press=self.on_press,
            on_release=self.on_release,
        )
        self.mouse_listener.start()
        self.keyboard_listener.start()
        logger.info('listening on %s:%s', self.host, self.port)
        logger.info('TLS fingerprint %s', link.fingerprint(self.tls_dir / 'cert.pem'))
        logger.info('trusted peer certificates: %d', len(peers))
        logger.info('screen %sx%s', self.screen_width, self.screen_height)
        logger.info('move into the edge facing a connected PC')
        # The default Windows timer can round the 4 ms motion wait to 15.6 ms.
        winmm = ctypes.windll.winmm
        timer_result = winmm.timeBeginPeriod(1)
        if timer_result:
            logger.warning('could not request 1 ms Windows timer resolution: %s', timer_result)
        try:
            async with link.serve(self.handle_client, self.host, self.port, ssl=ssl_context):
                await asyncio.Future()
        finally:
            if not timer_result:
                winmm.timeEndPeriod(1)

    def stop(self):
        self.running = False
        try:
            self._end_control(0.5, 0.5)
        finally:
            self._show_cursor()
        if hasattr(self, 'mouse_listener'):
            self.mouse_listener.stop()
        if hasattr(self, 'keyboard_listener'):
            self.keyboard_listener.stop()
        logger.info('server stopped')
