"""Secondary PC. Applies pointer and keyboard events from the primary."""
import asyncio
import ctypes
from ctypes import wintypes
import json
import logging
import msvcrt
import socket
import sys
from time import perf_counter

import link
from devices import Button, KeyController, MouseController
from power import PowerMonitor

from utils import decode_key, entry_point, get_screen_size, step_position

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004

BUTTONS = {
    'left': Button.left,
    'right': Button.right,
    'middle': Button.middle,
}


class MouseClient:
    def __init__(self, server_uri='ws://localhost:8765', position='right', tls_dir=None):
        self.server_uri = server_uri
        self.position = position
        # tls_dir is None only for the local input-cleanup test. Entry points always set it.
        self.tls_dir = tls_dir
        self.screen_width, self.screen_height = get_screen_size()
        self.running = False
        self.control_active = False
        self.websocket = None
        self.x = 0
        self.y = 0
        self.mouse = MouseController()
        self.keys = KeyController()
        self.pressed_keys = set()
        self.pressed_buttons = set()
        self.available = True
        self.loop = None
        self.power_monitor = None
        self._motion_color = False
        self._last_move_at = None
        self._last_move_gap = None
        self._last_motion_warning = float('-inf')

    def on_power_change(self, available):
        if self.loop is not None and not self.loop.is_closed():
            self.loop.call_soon_threadsafe(self._power_changed, available)

    def _power_changed(self, available):
        self.available = available
        if not available or not self.control_active:
            self.release_input()
        if self.websocket is not None:
            asyncio.create_task(self._send_ready(available))

    async def _send_ready(self, available=None):
        websocket = self.websocket
        if websocket is None:
            return
        if available is None:
            available = self.available
        try:
            await asyncio.wait_for(websocket.send(json.dumps(
                {'type': 'ready', 'ready': available})), 1)
        except (OSError, TimeoutError, link.WebSocketException):
            logger.warning('could not report client display/power state')

    def release_input(self):
        self.control_active = False
        self._last_move_at = self._last_move_gap = None
        self._last_motion_warning = float('-inf')
        for controller, held in ((self.keys, self.pressed_keys),
                                 (self.mouse, self.pressed_buttons)):
            for item in list(held):
                try:
                    controller.release(item)
                except Exception:
                    logger.exception('failed to release %r', item)
                else:
                    held.discard(item)

    def _show(self):
        width, height = self.screen_width, self.screen_height
        self.mouse.position = (
            int(min(max(self.x, 0), width - 1)),
            int(min(max(self.y, 0), height - 1)),
        )

    async def connect(self):
        self.loop = asyncio.get_running_loop()
        ssl_context = None
        if self.tls_dir is not None:
            if not link.peer_certs(self.tls_dir):
                raise ValueError(
                    'No paired server. Open run_sharemouse.cmd and pair using the server PIN.'
                )
            ssl_context = link.client_context(self.tls_dir)
        self.running = True
        self.power_monitor = PowerMonitor(self.on_power_change)
        console = None
        mode = wintypes.DWORD()
        if sys.stderr.isatty():
            console = wintypes.HANDLE(msvcrt.get_osfhandle(sys.stderr.fileno()))
            kernel32 = ctypes.windll.kernel32
            self._motion_color = bool(kernel32.GetConsoleMode(console, ctypes.byref(mode)) and
                                      kernel32.SetConsoleMode(
                                          console, mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING))
        try:
            self.power_monitor.start()
            await self._connection_loop(ssl_context)
        finally:
            try:
                self.power_monitor.stop()
                self.power_monitor = None
            finally:
                if self._motion_color:
                    kernel32.SetConsoleMode(console, mode.value)
                    self._motion_color = False

    async def _connection_loop(self, ssl_context):
        retry = 1
        while self.running:
            try:
                logger.info('connecting to %s', self.server_uri)
                async with link.connect(self.server_uri, ssl=ssl_context) as websocket:
                    self.websocket = websocket
                    await websocket.send(json.dumps({
                        'type': 'hello',
                        'position': self.position,
                        'machine': socket.gethostname(),
                        'ready': self.available,
                    }))
                    logger.info('connected, this PC is %s of the primary', self.position)
                    retry = 1
                    async for message in websocket:
                        await self.handle_message(message)
            except link.ConnectionClosed:
                logger.warning('connection closed')
            except Exception as e:
                logger.error('connection error: %s', e)
            finally:
                self.websocket = None
                self.release_input()
            if self.running:
                logger.info('reconnecting in %ss', retry)
                await asyncio.sleep(retry)
                retry = min(retry * 2, 30)

    async def handle_message(self, message):
        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            logger.error('invalid JSON')
            return
        if not isinstance(data, dict):
            return
        handler = {
            'heartbeat': self.handle_heartbeat,
            'control': self.handle_control,
            'move': self.handle_move,
            'click': self.handle_click,
            'scroll': self.handle_scroll,
            'key_press': self.handle_key_press,
            'key_release': self.handle_key_release,
        }.get(data.get('type'))
        if not handler:
            return
        try:
            await handler(data)
        except Exception as e:
            logger.error('%s: %s', data.get('type'), e)

    async def handle_heartbeat(self, data):
        await self.websocket.send(json.dumps({'type': 'heartbeat_ack', 'id': data.get('id'),
                                             'ready': self.available}))

    async def handle_control(self, data):
        if not data.get('active'):
            self.release_input()
            return
        if not self.available:
            await self._send_ready()
            return
        if self.control_active:
            return
        self.control_active = True
        self.x, self.y = entry_point(
            self.position,
            float(data.get('entry_x', 0.5)),
            float(data.get('entry_y', 0.5)),
            self.screen_width,
            self.screen_height,
        )
        try:
            self.mouse.position = (self.x, self.y)
        except OSError:
            self.available = False
            await self.return_control()
            await self._send_ready()
            raise
        logger.info('control active at (%s, %s)', self.x, self.y)

    async def handle_move(self, data):
        if not self.control_active:
            return
        received_at = perf_counter()
        start_x, start_y = self.x, self.y
        steps = data['steps'] if 'steps' in data else [(data['delta_x'], data['delta_y'])]
        leaving = False
        max_step = 0
        for dx, dy in steps:
            max_step = max(max_step, abs(dx), abs(dy))
            self.x, self.y, leaving = step_position(
                self.position, self.x, self.y, dx, dy, self.screen_width, self.screen_height)
            if leaving:
                break
        try:
            self._show()
        except OSError:
            self.available = False
            await self.return_control()
            await self._send_ready()
            raise
        self._report_motion(received_at, start_x, start_y, len(steps), max_step, leaving)
        if leaving:
            await self.return_control()

    def _report_motion(self, received_at, start_x, start_y, steps, max_step, leaving):
        apply_ms = (perf_counter() - received_at) * 1000
        gap_ms = None if self._last_move_at is None else (received_at - self._last_move_at) * 1000
        previous_gap = self._last_move_gap
        self._last_move_at, self._last_move_gap = received_at, gap_ms
        dx, dy = self.x - start_x, self.y - start_y
        jump = max(abs(dx), abs(dy))
        reasons = []
        # ponytail: fixed thresholds flag likely stalls, not intentional pauses with certainty.
        if not leaving and jump >= 32:
            if previous_gap is not None and previous_gap <= 40 and 60 <= gap_ms <= 500:
                reasons.append('pause+jump')
            if steps >= 8:
                reasons.append('bunched-moves')
        if max_step >= 100:
            reasons.append('large-step')
        if apply_ms >= 16:
            reasons.append('slow-apply')
        if reasons and received_at - self._last_motion_warning >= 0.5:
            self._last_motion_warning = received_at
            gap = 'first' if gap_ms is None else f'{gap_ms:.1f} ms'
            message = (f'[MOUSE] {", ".join(reasons)} | gap={gap} | '
                       f'apply={apply_ms:.1f} ms | steps={steps} | '
                       f'max_step={max_step:g} px | delta=({dx:g},{dy:g}) px')
            logger.warning('%s', f'\x1b[91m{message}\x1b[0m' if self._motion_color else message)

    async def return_control(self):
        if not self.websocket or not self.control_active:
            return
        self.release_input()
        shown_x = min(max(self.x, 0), self.screen_width - 1)
        shown_y = min(max(self.y, 0), self.screen_height - 1)
        try:
            await self.websocket.send(json.dumps({
                'type': 'return_control',
                'exit_x': shown_x / self.screen_width,
                'exit_y': shown_y / self.screen_height,
            }))
            logger.info('returned control')
        except Exception as e:
            logger.error('return failed: %s', e)

    async def handle_click(self, data):
        if not self.control_active:
            return
        button = BUTTONS.get(data.get('button'))
        if button is None:
            return
        if data.get('pressed'):
            self.mouse.press(button)
            self.pressed_buttons.add(button)
        else:
            self.mouse.release(button)
            self.pressed_buttons.discard(button)

    async def handle_scroll(self, data):
        if not self.control_active:
            return
        self.mouse.scroll(int(data.get('dx') or 0), int(data.get('dy') or 0))

    async def _key(self, data, pressed):
        if not self.control_active:
            return
        key = decode_key(data.get('key'))
        if key is None:
            return
        if pressed:
            self.keys.press(key)
            self.pressed_keys.add(key)
        else:
            self.keys.release(key)
            self.pressed_keys.discard(key)

    async def handle_key_press(self, data):
        await self._key(data, True)

    async def handle_key_release(self, data):
        await self._key(data, False)

    def stop(self):
        self.running = False
        self.release_input()
        if self.power_monitor is not None:
            self.power_monitor.stop()
        logger.info('client stopped')
