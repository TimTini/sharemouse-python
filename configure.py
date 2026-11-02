"""Save or inspect this computer's ShareMouse role without starting input hooks."""
import argparse
from datetime import datetime
import ipaddress
import json
from pathlib import Path
import re
import shutil
import socket
import sys

CONFIG_PATH = Path(__file__).resolve().with_name('config.json')
POSITIONS = ('right', 'left', 'top', 'bottom')


def validate_config(config):
    if not isinstance(config, dict) or config.get('version') != 1:
        raise ValueError('Unsupported configuration; run configure.py again.')
    if config.get('machine') != socket.gethostname():
        raise ValueError('Configuration belongs to another computer; run configure.py here.')
    if config.get('role') not in ('server', 'client'):
        raise ValueError('Role must be server or client.')
    host = config.get('host')
    if not isinstance(host, str) or not host:
        raise ValueError('Enter a server IP address or hostname.')
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if len(host) > 253 or not all(
            re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', label)
            for label in host.rstrip('.').split('.')
        ):
            raise ValueError('Host must be an IP address or hostname, without ws:// or port.')
    if config['role'] == 'client' and host in ('0.0.0.0', '::'):
        raise ValueError('Client needs the actual server IP, not a wildcard address.')
    port = config.get('port')
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError('Port must be an integer from 1 to 65535.')
    if config['role'] == 'client' and config.get('position') not in POSITIONS:
        raise ValueError('Position must be right, left, top or bottom.')
    return config


def load_config(path=CONFIG_PATH):
    return validate_config(json.loads(Path(path).read_text(encoding='utf-8')))


def server_uri(config):
    host = config['host']
    return f"ws://{'[' + host + ']' if ':' in host else host}:{config['port']}"


def show_config(config):
    print(f"Machine: {config['machine']} | Role: {config['role'].upper()}")
    if config['role'] == 'server':
        print(f"Controls clients; listen: {config['host']}:{config['port']}")
        try:
            addresses = sorted({info[4][0] for info in socket.getaddrinfo(
                socket.gethostname(), None, socket.AF_INET, socket.SOCK_STREAM)})
            print('Local IPv4 candidates (choose the LAN adapter): ' + ', '.join(addresses))
        except OSError:
            print('Cannot list local IPv4 addresses; use ipconfig (Windows) or ifconfig.')
        print('Connected client names/addresses appear in the running server log.')
    else:
        print(f"Controlled by server: {server_uri(config)} | Side: {config['position']}")
    print('Saved configuration only; this does not confirm a running connection.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=CONFIG_PATH)
    parser.add_argument('--show', action='store_true', help='show saved local role')
    parser.add_argument('--role', choices=('server', 'client'))
    parser.add_argument('--host', help='server bind address or client target IP/hostname')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--position', choices=POSITIONS)
    args = parser.parse_args(argv)
    try:
        if args.show:
            show_config(load_config(args.config))
            return 0
        role = args.role or input('Role [server/client]: ').strip().lower()
        host = args.host
        if host is None:
            host = (input('Listen IP [0.0.0.0]: ').strip() or '0.0.0.0') if role == 'server' else input('Server IP/hostname: ').strip()
        config = dict(version=1, machine=socket.gethostname(), role=role,
                      host=host, port=args.port)
        if role == 'client':
            config['position'] = args.position or input('Client side [right/left/top/bottom; default right]: ').strip() or 'right'
        validate_config(config)
        args.config.parent.mkdir(parents=True, exist_ok=True)
        if args.config.exists():
            backup = args.config.parent / 'backup'
            backup.mkdir(exist_ok=True)
            shutil.copy2(args.config, backup / f"config-{datetime.now():%Y%m%d-%H%M%S-%f}.json")
        temporary = args.config.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(config, indent=2) + '\n', encoding='utf-8')
        temporary.replace(args.config)
        print(f'Saved: {args.config.resolve()}')
        show_config(config)
        return 0
    except (OSError, ValueError, EOFError) as error:
        print(f'Configuration error: {error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
