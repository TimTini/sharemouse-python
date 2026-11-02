"""Run the server/client role saved by configure.py on this computer."""
import argparse
import link
import importlib
import importlib.metadata
import getpass
from pathlib import Path
import re
import sys

from configure import (
    CONFIG_PATH, load_config, main as configure_main, probe_server, server_uri, show_config, tls_directory,
)


def check_dependencies():
    try:
        lines = Path(__file__).with_name('requirements.txt').read_text(encoding='utf-8').splitlines()
    except OSError as error:
        print(f'Dependency check failed: {error}', file=sys.stderr)
        return 1
    for line in lines:
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        match = re.match(r'^([A-Za-z0-9_-]+)==([0-9.]+)', line)
        if match is None:
            continue  # SHA256 continuation lines belong to the preceding requirement.
        try:
            name, required = match.groups()
            installed = importlib.metadata.version(name)
            if installed != required:
                raise ImportError(f'{name} {required} is required; installed {installed}')
            importlib.import_module(name)
        except (ImportError, ValueError, AttributeError) as error:
            print(f'Dependency check failed: {error}', file=sys.stderr)
            return 1
    return 0


def pair_device(config, tls_dir):
    import pairing
    link.ensure_identity(tls_dir)
    if config['role'] == 'server':
        link.run(pairing.open_server(config['host'], config['port'], tls_dir))
    else:
        pin = getpass.getpass('Eight-digit PIN shown on the server: ').strip()
        link.run(pairing.pair_client(config['host'], config['port'], tls_dir, pin))
        print('Server paired. Normal control uses authenticated TLS.')


async def run(config):
    tls_dir = config.get('tls_dir')
    if config['role'] == 'server':
        from server import MouseServer
        kwargs = dict(host=config['host'], port=config['port'])
        if tls_dir:
            kwargs['tls_dir'] = tls_dir
        app = MouseServer(**kwargs)
        operation = app.start
    else:
        from client import MouseClient
        kwargs = dict(server_uri=server_uri(config), position=config['position'])
        if tls_dir:
            kwargs['tls_dir'] = tls_dir
        app = MouseClient(**kwargs)
        operation = app.connect
    try:
        await operation()
    finally:
        app.stop()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=CONFIG_PATH)
    parser.add_argument('--check', action='store_true', help='validate config only; no hooks or network')
    parser.add_argument('--interactive', action='store_true', help='show the double-click run/configure menu')
    parser.add_argument('--check-dependencies', action='store_true', help='check installed requirements without starting ShareMouse')
    args = parser.parse_args(argv)
    if args.check_dependencies:
        return check_dependencies()
    try:
        if args.interactive and not args.check:
            while True:
                print('\n1. Run ShareMouse\n2. Configure server/client\n3. Show saved configuration\n4. Pair new device (stop the server Run window first)\n0. Exit')
                choice = input('Select [0-4]: ').strip()
                if choice == '0':
                    return 0
                if choice == '2' or (choice == '1' and not args.config.exists()):
                    result = configure_main(['--config', str(args.config)])
                    if result:
                        print('Configuration was not saved. Please try again.')
                    continue
                if choice in ('1', '3', '4'):
                    try:
                        show_config(load_config(args.config), tls_directory(args.config))
                    except (OSError, ValueError) as error:
                        print(f'Cannot use saved configuration: {error}\nSelect 2 to configure this computer.')
                        continue
                    if choice == '4':
                        try:
                            pair_device(load_config(args.config), tls_directory(args.config))
                        except (OSError, ValueError) as error:
                            print(f'Pairing failed: {error}')
                        continue
                    if choice == '1':
                        config = load_config(args.config)
                        if config['role'] == 'server':
                            host = '127.0.0.1' if config['host'] == '0.0.0.0' else config['host']
                            if link.run(probe_server(host, config['port'], tls_directory(args.config))):
                                print('ShareMouse server is already running on this port. No duplicate started.')
                                return 0
                        break
                else:
                    print('Invalid selection.')
        config = load_config(args.config)
        if not args.check:
            tls_dir = tls_directory(args.config)
            link.ensure_identity(tls_dir)
            config['tls_dir'] = str(tls_dir)
            if not link.peer_certs(tls_dir):
                pair_device(config, tls_dir)
        show_config(config, tls_directory(args.config))
        if args.check:
            return 0
        print('Move the server mouse to the client-facing edge. Ctrl+C stops this process.')
        link.run(run(config))
        return 0
    except KeyboardInterrupt:
        return 0
    except (OSError, ValueError, ImportError, EOFError) as error:
        print(f'ShareMouse error: {error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
