"""Menu and real loopback discovery checks; never install packages or inject input."""
import asyncio
import contextlib
import io
import ipaddress
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from unittest.mock import AsyncMock, Mock, patch

import configure
import run_sharemouse


async def check_discovery():
    import websockets
    from server import MouseServer
    with patch('server.get_screen_size', return_value=(800, 600)):
        app = MouseServer()
    async with websockets.serve(app.handle_client, '127.0.0.1', 0) as listener:
        port = listener.sockets[0].getsockname()[1]
        found = await configure.discover_servers([ipaddress.ip_network('127.0.0.1/32')], port)
        assert len(found) == 1 and found[0]['host'] == '127.0.0.1'
        assert found[0]['machine'] and not app.clients and not app.control_active
    assert await configure.probe_server('127.0.0.1', port) is None
    with patch('configure.probe_server', new=AsyncMock()) as probe:
        for networks in ([ipaddress.ip_network('192.168.0.0/24'), ipaddress.ip_network('192.168.1.0/24')],
                         [ipaddress.ip_network('192.168.0.0/23')]):
            try:
                await configure.discover_servers(networks, port)
            except ValueError:
                pass
            else:
                raise AssertionError('Oversized scan accepted')
        probe.assert_not_called()

    async def unrelated(ws):
        await ws.recv()
        await ws.send(json.dumps({'type': 'other-service'}))
    async with websockets.serve(unrelated, '127.0.0.1', 0) as listener:
        assert await configure.probe_server('127.0.0.1', listener.sockets[0].getsockname()[1]) is None


def run():
    assert hasattr(configure, 'discover_servers'), 'LAN discovery is missing'
    with patch('run_sharemouse.importlib.metadata.version', return_value='0.0'), \
            contextlib.redirect_stderr(io.StringIO()):
        assert run_sharemouse.check_dependencies() == 1
    with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
        path = Path(folder) / 'config.json'
        with contextlib.redirect_stdout(io.StringIO()):
            with patch('builtins.input', side_effect=['x', '1', '']):
                assert configure.main(['--config', str(path)]) == 0
            assert configure.load_config(path)['role'] == 'server'
            with patch('builtins.input', side_effect=['1']), \
                    patch('run_sharemouse.probe_server', new=AsyncMock(return_value={'machine': 'SERVER-PC'})), \
                    patch('run_sharemouse.run', new=AsyncMock()) as start:
                assert run_sharemouse.main(['--config', str(path), '--interactive']) == 0
                start.assert_not_called()
            found = [{'host': '192.168.8.20', 'machine': 'SERVER-PC'}]
            with patch('configure.get_lan_networks', return_value=[ipaddress.ip_network('192.168.8.0/24')]), \
                    patch('configure.discover_servers', new=AsyncMock(return_value=found)), \
                    patch('builtins.input', side_effect=['2', '', '1', '2']):
                assert configure.main(['--config', str(path)]) == 0
            config = configure.load_config(path)
            assert config['host'] == '192.168.8.20' and config['position'] == 'left'
            with patch('configure.get_lan_networks', return_value=[]), \
                    patch('builtins.input', side_effect=['2', '', '0', '192.168.8.99', '4']):
                assert configure.main(['--config', str(path)]) == 0
            assert configure.load_config(path)['position'] == 'bottom'
            with patch('builtins.input', side_effect=['0']):
                assert run_sharemouse.main(['--config', str(path), '--interactive']) == 0
            with patch('builtins.input', side_effect=['1']), \
                    patch('run_sharemouse.run', new=AsyncMock()) as start:
                assert run_sharemouse.main(['--config', str(path), '--interactive']) == 0
                assert start.call_args.args[0]['host'] == '192.168.8.99'

    records = [{'Address': '192.168.8.19', 'PrefixLength': 24},
               {'Address': '100.64.0.1', 'PrefixLength': 10},
               {'Address': '127.0.0.1', 'PrefixLength': 24}]
    with patch('configure.platform.system', return_value='Windows'), \
            patch('configure.subprocess.run', return_value=Mock(stdout=json.dumps(records))):
        assert configure.get_lan_networks() == [ipaddress.ip_network('192.168.8.0/24')]
    asyncio.run(check_discovery())
    # Exercise actual CMD entrypoints without changing config or starting hooks.
    if __import__('sys').platform == 'win32' and Path(__file__).with_name('.venv').exists():
        root = Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory(prefix='launcher space ', dir=root) as folder:
            target = Path(folder)
            assert target.resolve().is_relative_to(root)
            scripts = target / '.venv' / 'Scripts'
            scripts.mkdir(parents=True)
            shutil.copy2(root / '.venv/Scripts/python.exe', scripts / 'python.exe')
            shutil.copy2(root / '.venv/pyvenv.cfg', target / '.venv/pyvenv.cfg')
            for name in ('configure.cmd', 'run_sharemouse.cmd', 'configure.py', 'run_sharemouse.py', 'requirements.txt'):
                shutil.copy2(root / name, target / name)
            env = dict(os.environ, PYTHONPATH=str(root / '.venv/Lib/site-packages'))
            assert run_sharemouse.check_dependencies() == 0, 'Prepare dependencies before running CMD checks; tests do not install them'
            for cwd in (target,):
                for name in ('configure.cmd', 'run_sharemouse.cmd'):
                    result = subprocess.run(['cmd.exe', '/d', '/c', name], cwd=cwd,
                                            input='0\n\n', capture_output=True, text=True, env=env,
                                            encoding='utf-8', errors='replace', timeout=30)
                    assert result.returncode == 0, result.stdout + result.stderr
    print('launcher menus, CMD and discovery: ok')


if __name__ == '__main__':
    run()
