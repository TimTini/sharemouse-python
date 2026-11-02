"""Primary PC. Watches the pointer and hands it to a peer across a screen edge."""
import asyncio
import ctypes
import json
import logging
import platform
import socket
import threading

from pynput import keyboard, mouse
import websockets

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


class MouseServer:
    def __init__(self, host='0.0.0.0', port=8765, edge_threshold=5):
        self.host = host
        self.port = port
        self.edge_threshold = edge_threshold
        self.screen_width, self.screen_height = get_screen_size()
        self.clients = {}
        self.running = False
        self.control_active = False
        self.active_edge = None
        self.loop = None
        self._lock = threading.Lock()
        self._placing = False
        self._cursor_hidden = False
        self.park_x = self.screen_width // 2
        self.park_y = self.screen_height // 2

    async def _send(self, targets, message):
        if targets:
            await asyncio.gather(
                *(ws.send(message) for ws in targets),
                return_exceptions=True,
            )

    def _emit(self, payload):
        with self._lock:
            edge = self.active_edge
            targets = [ws for ws, pos in self.clients.items() if pos == edge]
        if targets and self.loop is not None:
            asyncio.run_coroutine_threadsafe(
                self._send(list(targets), json.dumps(payload)),
                self.loop,
            )

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
        with self._lock:
            if not self.control_active:
                return
            edge = self.active_edge
            self.control_active = False
            self.active_edge = None
        try:
            exit_x, exit_y = float(exit_x), float(exit_y)
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
        with self._lock:
            if self.control_active:
                return
            self.control_active = True
            self.active_edge = edge
        self._capture()
        self._emit({
            'type': 'control',
            'active': True,
            'entry_x': entry_x,
            'entry_y': entry_y,
        })
        logger.info('control -> %s', edge)

    def on_move(self, x, y, injected=False):
        if not self.running or self._placing or injected:
            return
        try:
            self._on_move(x, y)
        except Exception:
            logger.exception('move')

    def _on_move(self, x, y):
        if not self.control_active:
            with self._lock:
                edges = set(self.clients.values())
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

    def _keyboard_filter(self, msg, data):
        listener = self.keyboard_listener
        mask = (
            listener._KBDLLHOOKSTRUCT.LLKHF_INJECTED
            | listener._KBDLLHOOKSTRUCT.LLKHF_LOWER_IL_INJECTED
        )
        if data.flags & mask or not self.control_active:
            return True
        try:
            if msg in listener._PRESS_MESSAGES or msg in listener._RELEASE_MESSAGES:
                try:
                    key = listener._event_to_key(msg, data.vkCode)
                except Exception:
                    key = None
                if key is not None:
                    kind = 'key_press' if msg in listener._PRESS_MESSAGES else 'key_release'
                    self._emit_key(kind, key)
        finally:
            listener.suppress_event()

    def on_press(self, key, injected=False):
        if platform.system() == 'Windows' or injected or not self.control_active:
            return
        self._emit_key('key_press', key)

    def on_release(self, key, injected=False):
        if platform.system() == 'Windows' or injected or not self.control_active:
            return
        self._emit_key('key_release', key)

    async def handle_client(self, websocket):
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
                        replaced = any(
                            other is not websocket and other_pos == pos
                            for other, other_pos in self.clients.items()
                        )
                        for other, other_pos in list(self.clients.items()):
                            if other is not websocket and other_pos == pos:
                                self.clients.pop(other, None)
                        self.clients[websocket] = pos
                        count = len(self.clients)
                        drop = replaced and self.control_active and self.active_edge == pos
                    if drop:
                        self._end_control(0.5, 0.5)
                    logger.info('client %r at %s on %s (%d connected)',
                                data.get('machine', 'unknown'), websocket.remote_address, pos, count)
                elif kind == 'return_control':
                    with self._lock:
                        owns = (
                            self.control_active
                            and self.clients.get(websocket) == self.active_edge
                        )
                    if owns:
                        self._end_control(data.get('exit_x', 0.5), data.get('exit_y', 0.5))
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            with self._lock:
                self.clients.pop(websocket, None)
                orphan = self.control_active and not any(
                    pos == self.active_edge for pos in self.clients.values()
                )
                count = len(self.clients)
            if orphan:
                self._end_control(0.5, 0.5)
            logger.info('client left (%d connected)', count)

    async def start(self):
        self.running = True
        self.loop = asyncio.get_running_loop()
        self.mouse_listener = mouse.Listener(
            on_move=self.on_move,
            on_click=self.on_click,
            on_scroll=self.on_scroll,
        )
        self.keyboard_listener = keyboard.Listener(
            on_press=self.on_press,
            on_release=self.on_release,
            win32_event_filter=self._keyboard_filter,
        )
        self.mouse_listener.start()
        self.keyboard_listener.start()
        logger.info('listening on %s:%s', self.host, self.port)
        logger.info('screen %sx%s', self.screen_width, self.screen_height)
        logger.info('move into the edge facing a connected PC')
        async with websockets.serve(self.handle_client, self.host, self.port):
            await asyncio.Future()

    def stop(self):
        self.running = False
        self._show_cursor()
        if hasattr(self, 'mouse_listener'):
            self.mouse_listener.stop()
        if hasattr(self, 'keyboard_listener'):
            self.keyboard_listener.stop()
        logger.info('server stopped')


if __name__ == '__main__':
    server = MouseServer()
    try:
        asyncio.run(server.start())
    except KeyboardInterrupt:
        logger.info('server shutdown')
    finally:
        server.stop()
