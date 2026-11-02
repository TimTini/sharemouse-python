"""Windows mouse and keyboard. Stdlib and ctypes only."""
import ctypes
from ctypes import wintypes
import enum
import logging
import platform
import threading

logger = logging.getLogger(__name__)

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong
LRESULT = ctypes.c_ssize_t
HOOKPROC = ctypes.WINFUNCTYPE(wintypes.LPARAM, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
KEYUP = 0x0002
EXTENDED = 0x0001
UNICODE = 0x0004
WHEEL = 0x0800
HWHEEL = 0x1000
WHEEL_DELTA = 120

# Low-level hooks report these flag bits when the event was injected.
INJECTED_KEY = 0x10 | 0x02
INJECTED_MOUSE = 0x01 | 0x02


class Swallow(Exception):
    """Raised from a hook callback so Windows does not deliver that event."""


class MOUSEINPUT(ctypes.Structure):
    _fields_ = (
        ('dx', wintypes.LONG),
        ('dy', wintypes.LONG),
        ('mouseData', wintypes.DWORD),
        ('dwFlags', wintypes.DWORD),
        ('time', wintypes.DWORD),
        ('dwExtraInfo', ULONG_PTR),
    )


class KEYBDINPUT(ctypes.Structure):
    _fields_ = (
        ('wVk', wintypes.WORD),
        ('wScan', wintypes.WORD),
        ('dwFlags', wintypes.DWORD),
        ('time', wintypes.DWORD),
        ('dwExtraInfo', ULONG_PTR),
    )


class _INPUT_UNION(ctypes.Union):
    _fields_ = (('mi', MOUSEINPUT), ('ki', KEYBDINPUT))


class INPUT(ctypes.Structure):
    _anonymous_ = ('_u',)
    _fields_ = (('type', wintypes.DWORD), ('_u', _INPUT_UNION))


# Wrong padding here injects clicks at the wrong size and Windows drops them.
_EXPECTED_INPUT = 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28
if ctypes.sizeof(INPUT) != _EXPECTED_INPUT:
    raise RuntimeError(
        f'SendInput struct is {ctypes.sizeof(INPUT)} bytes, expected {_EXPECTED_INPUT}'
    )


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = (
        ('pt', wintypes.POINT),
        ('mouseData', wintypes.DWORD),
        ('flags', wintypes.DWORD),
        ('time', wintypes.DWORD),
        ('dwExtraInfo', ULONG_PTR),
    )


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = (
        ('vkCode', wintypes.DWORD),
        ('scanCode', wintypes.DWORD),
        ('flags', wintypes.DWORD),
        ('time', wintypes.DWORD),
        ('dwExtraInfo', ULONG_PTR),
    )


user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
user32.SendInput.restype = wintypes.UINT
user32.SetWindowsHookExW.argtypes = (ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD)
user32.SetWindowsHookExW.restype = ctypes.c_void_p
kernel32.GetLastError.restype = wintypes.DWORD
user32.UnhookWindowsHookEx.argtypes = (ctypes.c_void_p,)
user32.CallNextHookEx.argtypes = (ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
user32.CallNextHookEx.restype = LRESULT
user32.GetMessageW.argtypes = (ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT)
user32.GetMessageW.restype = ctypes.c_int
user32.PeekMessageW.argtypes = (
    ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT,
)
user32.PeekMessageW.restype = wintypes.BOOL
user32.PostThreadMessageW.argtypes = (wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
user32.MapVirtualKeyW.argtypes = (wintypes.UINT, wintypes.UINT)
user32.MapVirtualKeyW.restype = wintypes.UINT


def _require_windows():
    if platform.system() != 'Windows':
        raise OSError('Mouse and keyboard control is implemented for Windows only.')


def _send(event):
    sent = user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(INPUT))
    if sent != 1:
        raise OSError('SendInput failed')


class KeyCode:
    def __init__(self, vk=None, char=None, extended=False):
        self.vk = None if vk is None else int(vk)
        self.char = char
        self.extended = bool(extended)

    @classmethod
    def from_vk(cls, vk, char=None):
        return cls(vk=vk, char=char)

    @classmethod
    def from_char(cls, char):
        return cls(char=char)


class Key(enum.Enum):
    """Names on the wire. Value is (virtual-key, extended flag)."""

    alt_l = (0xA4, False)
    alt_r = (0xA5, True)
    alt = (0x12, False)
    alt_gr = (0xA5, False)
    ctrl_l = (0xA2, False)
    ctrl_r = (0xA3, True)
    ctrl = (0x11, False)
    shift_l = (0xA0, False)
    shift_r = (0xA1, False)
    shift = (0xA0, False)
    cmd_l = (0x5B, False)
    cmd_r = (0x5C, False)
    cmd = (0x5B, False)
    backspace = (0x08, False)
    tab = (0x09, False)
    enter = (0x0D, False)
    pause = (0x13, False)
    caps_lock = (0x14, False)
    esc = (0x1B, False)
    space = (0x20, False)
    page_up = (0x21, True)
    page_down = (0x22, True)
    end = (0x23, True)
    home = (0x24, True)
    left = (0x25, True)
    up = (0x26, True)
    right = (0x27, True)
    down = (0x28, True)
    print_screen = (0x2C, True)
    insert = (0x2D, True)
    delete = (0x2E, True)
    menu = (0x5D, False)
    f1 = (0x70, False)
    f2 = (0x71, False)
    f3 = (0x72, False)
    f4 = (0x73, False)
    f5 = (0x74, False)
    f6 = (0x75, False)
    f7 = (0x76, False)
    f8 = (0x77, False)
    f9 = (0x78, False)
    f10 = (0x79, False)
    f11 = (0x7A, False)
    f12 = (0x7B, False)
    f13 = (0x7C, False)
    f14 = (0x7D, False)
    f15 = (0x7E, False)
    f16 = (0x7F, False)
    f17 = (0x80, False)
    f18 = (0x81, False)
    f19 = (0x82, False)
    f20 = (0x83, False)
    f21 = (0x84, False)
    f22 = (0x85, False)
    f23 = (0x86, False)
    f24 = (0x87, False)
    num_lock = (0x90, True)
    scroll_lock = (0x91, False)
    media_volume_mute = (0xAD, True)
    media_volume_down = (0xAE, True)
    media_volume_up = (0xAF, True)
    media_next = (0xB0, True)
    media_previous = (0xB1, True)
    media_stop = (0xB2, True)
    media_play_pause = (0xB3, True)

    @property
    def vk(self):
        return self.value[0]

    @property
    def extended(self):
        return self.value[1]


# First name for a virtual key is the one a physical key reports.
BY_VK = {}
for _key in Key:
    BY_VK.setdefault(_key.vk, _key)


def key_from_vk(vk, flags):
    if vk == 0x10:
        return Key.shift_r if flags & 1 else Key.shift_l
    if vk == 0x11:
        return Key.ctrl_r if flags & 1 else Key.ctrl_l
    if vk == 0x12:
        return Key.alt_r if flags & 1 else Key.alt_l
    found = BY_VK.get(vk)
    if found is not None:
        return found
    return KeyCode.from_vk(vk)


def _key_parts(key):
    if isinstance(key, Key):
        return key.vk, key.extended, None
    vk = getattr(key, 'vk', None)
    if vk:
        return int(vk), bool(getattr(key, 'extended', False)), None
    char = getattr(key, 'char', None)
    if not char:
        raise ValueError('key has no virtual key or character')
    return None, False, char


class KeyController:
    def press(self, key):
        self._send(key, True)

    def release(self, key):
        self._send(key, False)

    def _send(self, key, pressed):
        _require_windows()
        vk, extended, char = _key_parts(key)
        flags = 0 if pressed else KEYUP
        if char is not None:
            _send(INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(
                wVk=0, wScan=ord(char), dwFlags=flags | UNICODE,
            )))
            return
        if extended:
            flags |= EXTENDED
        scan = user32.MapVirtualKeyW(vk, 0)
        _send(INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(
            wVk=vk, wScan=scan, dwFlags=flags,
        )))


class Button(enum.Enum):
    left = (0x0002, 0x0004)
    right = (0x0008, 0x0010)
    middle = (0x0020, 0x0040)

    @property
    def down_flag(self):
        return self.value[0]

    @property
    def up_flag(self):
        return self.value[1]


class MouseController:
    @property
    def position(self):
        _require_windows()
        point = wintypes.POINT()
        if not user32.GetCursorPos(ctypes.byref(point)):
            raise OSError('GetCursorPos failed')
        return point.x, point.y

    @position.setter
    def position(self, pos):
        _require_windows()
        if not user32.SetCursorPos(int(pos[0]), int(pos[1])):
            raise OSError('SetCursorPos failed')

    def press(self, button):
        self._button(button, True)

    def release(self, button):
        self._button(button, False)

    def _button(self, button, pressed):
        _require_windows()
        flag = button.down_flag if pressed else button.up_flag
        _send(INPUT(type=INPUT_MOUSE, mi=MOUSEINPUT(dwFlags=flag)))

    def scroll(self, dx, dy):
        _require_windows()
        dx, dy = int(dx), int(dy)
        if dy:
            _send(INPUT(type=INPUT_MOUSE, mi=MOUSEINPUT(
                mouseData=dy * WHEEL_DELTA, dwFlags=WHEEL,
            )))
        if dx:
            _send(INPUT(type=INPUT_MOUSE, mi=MOUSEINPUT(
                mouseData=dx * WHEEL_DELTA, dwFlags=HWHEEL,
            )))


class _HookThread(threading.Thread):
    def __init__(self, hook_id, dispatch):
        super().__init__(name=f'hook-{hook_id}', daemon=True)
        self._hook_id = hook_id
        self._dispatch = dispatch
        self._ready = threading.Event()
        self._hook = None
        self._error = None
        # Keep the callback alive for as long as the hook exists.
        self._proc = HOOKPROC(self._on_hook)

    def _on_hook(self, code, wparam, lparam):
        if code == 0:
            try:
                self._dispatch(wparam, lparam)
            except Swallow:
                return 1
            except Exception:
                logger.exception('input hook')
        return user32.CallNextHookEx(None, code, wparam, lparam)

    def run(self):
        # Low-level hooks run in this process, so the module handle stays null.
        self._hook = user32.SetWindowsHookExW(self._hook_id, self._proc, None, 0)
        if not self._hook:
            self._error = kernel32.GetLastError() or 'SetWindowsHookEx failed'
            self._ready.set()
            return
        # Create the thread queue before callers are allowed to post WM_QUIT.
        user32.PeekMessageW(ctypes.byref(wintypes.MSG()), None, 0, 0, 0)
        self._ready.set()
        message = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(message))
            user32.DispatchMessageW(ctypes.byref(message))
        user32.UnhookWindowsHookEx(self._hook)

    def start_hook(self):
        _require_windows()
        self.start()
        if not self._ready.wait(2) or not self._hook:
            raise OSError(f'input hook {self._hook_id} failed ({self._error})')

    def stop(self):
        if self.is_alive() and self.ident:
            user32.PostThreadMessageW(self.ident, 0x0012, 0, 0)
            self.join(timeout=2)


class MouseListener:
    def __init__(self, on_move, on_click, on_scroll):
        self.on_move = on_move
        self.on_click = on_click
        self.on_scroll = on_scroll
        self._thread = None

    def suppress_event(self):
        raise Swallow()

    def start(self):
        self._thread = _HookThread(14, self._dispatch)
        self._thread.start_hook()

    def stop(self):
        if self._thread:
            self._thread.stop()

    def _dispatch(self, msg, lpdata):
        data = ctypes.cast(lpdata, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
        injected = bool(data.flags & INJECTED_MOUSE)
        x, y = int(data.pt.x), int(data.pt.y)
        clicks = {
            0x0201: (Button.left, True),
            0x0202: (Button.left, False),
            0x0204: (Button.right, True),
            0x0205: (Button.right, False),
            0x0207: (Button.middle, True),
            0x0208: (Button.middle, False),
        }
        if msg == 0x0200:
            self.on_move(x, y, injected)
        elif msg in clicks:
            button, pressed = clicks[msg]
            self.on_click(x, y, button, pressed, injected)
        elif msg in (0x020A, 0x020E):
            amount = ctypes.c_short(data.mouseData >> 16).value // WHEEL_DELTA
            if msg == 0x020A:
                self.on_scroll(x, y, 0, amount, injected)
            else:
                self.on_scroll(x, y, amount, 0, injected)


class KeyListener:
    def __init__(self, on_press, on_release):
        self.on_press = on_press
        self.on_release = on_release
        self._thread = None

    def suppress_event(self):
        raise Swallow()

    def start(self):
        self._thread = _HookThread(13, self._dispatch)
        self._thread.start_hook()

    def stop(self):
        if self._thread:
            self._thread.stop()

    def _dispatch(self, msg, lpdata):
        data = ctypes.cast(lpdata, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
        injected = bool(data.flags & INJECTED_KEY)
        key = key_from_vk(int(data.vkCode), int(data.flags))
        if msg in (0x0100, 0x0104):
            self.on_press(key, injected)
        elif msg in (0x0101, 0x0105):
            self.on_release(key, injected)
