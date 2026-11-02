"""Edge handoff math shared by the primary and secondary PCs."""
import platform

from pynput.keyboard import Key, KeyCode

EDGE_THRESHOLD = 5
POSITIONS = ('left', 'right', 'top', 'bottom')


def get_screen_size():
    import pyautogui
    size = pyautogui.size()
    return size.width, size.height


def place_cursor(x, y):
    x, y = int(x), int(y)
    if platform.system() == 'Windows':
        import ctypes
        ctypes.windll.user32.SetCursorPos(x, y)
        return
    import pyautogui
    pyautogui.FAILSAFE = False
    pyautogui.PAUSE = 0
    pyautogui.moveTo(x, y)


def normalize_position(x, y, screen_width, screen_height):
    return x / screen_width, y / screen_height


def _clamp(value, limit):
    return max(0, min(int(value), limit - 1))


def hit_edge(x, y, width, height, edges, threshold=EDGE_THRESHOLD):
    """Edge the pointer is on, among `edges`. A corner goes to the deeper side."""
    scored = []
    if 'right' in edges and x >= width - threshold:
        scored.append((x - (width - threshold), 'right'))
    if 'left' in edges and x <= threshold:
        scored.append((threshold - x, 'left'))
    if 'bottom' in edges and y >= height - threshold:
        scored.append((y - (height - threshold), 'bottom'))
    if 'top' in edges and y <= threshold:
        scored.append((threshold - y, 'top'))
    if not scored:
        return None
    scored.sort(reverse=True)
    return scored[0][1]


def entry_point(position, entry_x, entry_y, width, height):
    """Where the pointer appears on the secondary, on the edge facing the primary."""
    if position == 'right':
        return 0, _clamp(entry_y * height, height)
    if position == 'left':
        return width - 1, _clamp(entry_y * height, height)
    if position == 'top':
        return _clamp(entry_x * width, width), height - 1
    if position == 'bottom':
        return _clamp(entry_x * width, width), 0
    return width // 2, height // 2


def return_point(edge, exit_x, exit_y, width, height, threshold=EDGE_THRESHOLD):
    """Primary pointer spot just inside `edge`, so the handoff does not immediately re-fire."""
    inset = threshold + 1
    if edge == 'right':
        return width - inset, _clamp(exit_y * height, height)
    if edge == 'left':
        return inset, _clamp(exit_y * height, height)
    if edge == 'top':
        return _clamp(exit_x * width, width), inset
    if edge == 'bottom':
        return _clamp(exit_x * width, width), height - inset
    return _clamp(exit_x * width, width), _clamp(exit_y * height, height)


def crossed_back(position, x, y, width, height, threshold=EDGE_THRESHOLD):
    """True once the secondary pointer has moved through the gutter back toward the primary."""
    if position == 'right':
        return x < -threshold
    if position == 'left':
        return x >= width + threshold
    if position == 'top':
        return y >= height + threshold
    if position == 'bottom':
        return y < -threshold
    return False


def step_position(position, x, y, dx, dy, width, height):
    """Apply a delta. Only the edge facing the primary may leave the screen."""
    x += dx
    y += dy
    if position == 'right':
        x = min(x, width - 1)
        y = min(max(y, 0), height - 1)
    elif position == 'left':
        x = max(x, 0)
        y = min(max(y, 0), height - 1)
    elif position == 'top':
        x = min(max(x, 0), width - 1)
        y = max(y, 0)
    elif position == 'bottom':
        x = min(max(x, 0), width - 1)
        y = min(y, height - 1)
    else:
        x = min(max(x, 0), width - 1)
        y = min(max(y, 0), height - 1)
    return x, y, crossed_back(position, x, y, width, height)


def encode_key(key):
    if isinstance(key, Key):
        return {'key': key.name}
    vk = getattr(key, 'vk', None)
    if vk is not None:
        return {'vk': int(vk)}
    char = getattr(key, 'char', None)
    if char:
        return {'char': char}
    return None


def decode_key(data):
    if not isinstance(data, dict):
        return None
    if 'key' in data:
        try:
            return Key[data['key']]
        except KeyError:
            return None
    if 'vk' in data:
        return KeyCode.from_vk(int(data['vk']))
    char = data.get('char')
    if char:
        return KeyCode.from_char(char)
    return None
