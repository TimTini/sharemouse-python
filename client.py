"""Secondary PC. Applies pointer and keyboard events from the primary."""
import asyncio
import json
import logging
import socket
import sys

from pynput import keyboard, mouse
from pynput.mouse import Button
import websockets

from utils import decode_key, entry_point, get_screen_size, step_position

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

BUTTONS = {
    'left': Button.left,
    'right': Button.right,
    'middle': Button.middle,
}


class MouseClient:
    def __init__(self, server_uri='ws://localhost:8765', position='right'):
        self.server_uri = server_uri
        self.position = position
        self.screen_width, self.screen_height = get_screen_size()
        self.running = False
        self.control_active = False
        self.websocket = None
        self.x = 0
        self.y = 0
        self.mouse = mouse.Controller()
        self.keys = keyboard.Controller()
        self.pressed_keys = set()
        self.pressed_buttons = set()

    def release_input(self):
        self.control_active = False
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
        self.running = True
        retry = 1
        while self.running:
            try:
                logger.info('connecting to %s', self.server_uri)
                async with websockets.connect(self.server_uri) as websocket:
                    self.websocket = websocket
                    await websocket.send(json.dumps({
                        'type': 'hello',
                        'position': self.position,
                        'machine': socket.gethostname(),
                    }))
                    logger.info('connected, this PC is %s of the primary', self.position)
                    retry = 1
                    async for message in websocket:
                        await self.handle_message(message)
            except websockets.exceptions.ConnectionClosed:
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
        handler = {
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

    async def handle_control(self, data):
        if not data.get('active') or self.control_active:
            return
        self.control_active = True
        self.x, self.y = entry_point(
            self.position,
            float(data.get('entry_x', 0.5)),
            float(data.get('entry_y', 0.5)),
            self.screen_width,
            self.screen_height,
        )
        self.mouse.position = (self.x, self.y)
        logger.info('control active at (%s, %s)', self.x, self.y)

    async def handle_move(self, data):
        if not self.control_active:
            return
        self.x, self.y, leaving = step_position(
            self.position,
            self.x,
            self.y,
            data['delta_x'],
            data['delta_y'],
            self.screen_width,
            self.screen_height,
        )
        self._show()
        if leaving:
            await self.return_control()

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
        logger.info('client stopped')


if __name__ == '__main__':
    server_uri = sys.argv[1] if len(sys.argv) > 1 else 'ws://localhost:8765'
    position = sys.argv[2] if len(sys.argv) > 2 else 'right'
    if position not in ('left', 'right', 'top', 'bottom'):
        logger.error('position must be left, right, top, or bottom')
        sys.exit(2)
    client = MouseClient(server_uri, position)
    try:
        asyncio.run(client.connect())
    except KeyboardInterrupt:
        logger.info('client shutdown')
    finally:
        client.stop()
