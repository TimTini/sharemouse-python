"""Pure checks for edge handoff and key encoding. No display, no network."""
from devices import Key, KeyCode

from utils import (
    EDGE_THRESHOLD,
    crossed_back,
    decode_key,
    encode_key,
    entry_point,
    hit_edge,
    normalize_position,
    return_point,
    step_position,
)


def run():
    width, height = 1920, 1080
    assert hit_edge(1918, 10, width, height, {'left'}) is None
    assert hit_edge(1918, 10, width, height, {'right'}) == 'right'
    assert hit_edge(2, 10, width, height, {'left'}) == 'left'
    assert hit_edge(10, 1078, width, height, {'bottom'}) == 'bottom'
    assert hit_edge(10, 1, width, height, {'top'}) == 'top'
    # Corner: top is 5px past, right is 4px past.
    assert hit_edge(1919, 0, width, height, {'right', 'top'}) == 'top'
    assert hit_edge(100, 100, width, height, set()) is None

    x, y = return_point('right', 0.2, 0.4, width, height)
    assert x == width - EDGE_THRESHOLD - 1
    assert y == int(0.4 * height)
    assert hit_edge(x, y, width, height, {'right'}) is None

    assert entry_point('right', 0.25, 0.5, 800, 600) == (0, 300)
    assert entry_point('left', 0.25, 0.5, 800, 600) == (799, 300)
    assert entry_point('top', 0.25, 0.5, 800, 600) == (200, 599)
    assert entry_point('bottom', 0.25, 0.5, 800, 600) == (200, 0)

    assert not crossed_back('right', 0, 10, 800, 600)
    assert not crossed_back('right', -EDGE_THRESHOLD, 10, 800, 600)
    assert crossed_back('right', -EDGE_THRESHOLD - 1, 10, 800, 600)
    assert crossed_back('left', 800 + EDGE_THRESHOLD, 10, 800, 600)
    assert crossed_back('top', 10, 600 + EDGE_THRESHOLD, 800, 600)
    assert crossed_back('bottom', 10, -EDGE_THRESHOLD - 1, 800, 600)
    assert not crossed_back('nope', -100, -100, 800, 600)

    x, y, leaving = step_position('right', 0, 10, -3, 0, 800, 600)
    assert (x, y, leaving) == (-3, 10, False)
    x, y, leaving = step_position('right', x, y, -3, 0, 800, 600)
    assert leaving and x == -6
    # Far edge stays on screen; the return edge is the only gutter.
    x, y, leaving = step_position('right', 790, 10, 50, -40, 800, 600)
    assert (x, y, leaving) == (799, 0, False)

    assert normalize_position(960, 540, width, height) == (0.5, 0.5)
    assert encode_key(Key.ctrl_l) == {'key': 'ctrl_l'}
    assert encode_key(KeyCode.from_vk(65)) == {'vk': 65}
    assert decode_key({'key': 'enter'}) == Key.enter
    assert decode_key({'vk': 65}).vk == 65
    assert decode_key({'char': 'a'}).char == 'a'
    assert decode_key({'key': 'not-a-key'}) is None
    assert encode_key(object()) is None


if __name__ == '__main__':
    run()
    print('ok')
