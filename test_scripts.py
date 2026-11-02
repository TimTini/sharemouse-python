"""Offline checks: no real listeners, network connections or input injection."""
import asyncio
import contextlib
import io
import json
from pathlib import Path
import tempfile
from unittest.mock import Mock, patch


def run():
    assert Path(__file__).with_name('configure.py').is_file(), 'Missing configuration script'
    import configure
    import run_sharemouse

    with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
        path = Path(folder) / 'config.json'
        with contextlib.redirect_stdout(io.StringIO()):
            assert configure.main(['--role', 'server', '--host', '0.0.0.0',
                                   '--port', '9876', '--config', str(path)]) == 0
            config = configure.load_config(path)
            assert config['role'] == 'server' and config['port'] == 9876
            server = Mock()
            server.start = Mock(side_effect=RuntimeError('startup failed'))
            with patch('server.MouseServer', return_value=server) as factory:
                try:
                    asyncio.run(run_sharemouse.run(config))
                except RuntimeError as error:
                    assert str(error) == 'startup failed'
                else:
                    raise AssertionError('Startup error was swallowed')
                factory.assert_called_once_with(host='0.0.0.0', port=9876)
                server.stop.assert_called_once()

            assert configure.main(['--role', 'client', '--host', '192.168.1.20',
                                   '--position', 'left', '--config', str(path)]) == 0
            assert len(list((path.parent / 'backup').glob('config-*.json'))) == 1
            config = configure.load_config(path)
            assert config['position'] == 'left'
            assert configure.server_uri(dict(config, host='::1')) == 'ws://[::1]:8765'
            assert run_sharemouse.main(['--config', str(path), '--check']) == 0
            from unittest.mock import AsyncMock
            client = Mock()
            client.connect = AsyncMock()
            with patch('client.MouseClient', return_value=client) as factory:
                asyncio.run(run_sharemouse.run(config))
                factory.assert_called_once_with(server_uri='ws://192.168.1.20:8765',
                                                position='left')
                client.stop.assert_called_once()
            assert configure.main(['--show', '--config', str(path)]) == 0
            with patch('configure.local_ipv4_addresses', return_value=[]):
                configure.show_config(dict(config, role='server'))
            for host in ('', 'ws://bad', 'host/path', 'user@host', 'bad host',
                         '8.8.8.8', 'example.com'):
                invalid = dict(config, host=host)
                try:
                    configure.validate_config(invalid)
                except ValueError:
                    pass
                else:
                    raise AssertionError(f'Accepted invalid host: {host!r}')
            for changes in ({'port': True}, {'port': 0}, {'position': 'diagonal'},
                            {'role': 'other'}, {'machine': 'another-computer'}):
                try:
                    configure.validate_config(dict(config, **changes))
                except ValueError:
                    pass
                else:
                    raise AssertionError(f'Accepted invalid config: {changes}')
            path.write_text(json.dumps({'role': 'other'}), encoding='utf-8')
            with contextlib.redirect_stderr(io.StringIO()):
                assert run_sharemouse.main(['--config', str(path), '--check']) == 2
                assert configure.main(['--show', '--config', str(path)]) == 2
    print('scripts: ok')


if __name__ == '__main__':
    run()
