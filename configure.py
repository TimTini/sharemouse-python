"""Save or inspect this computer's ShareMouse role without starting input hooks."""
import argparse
import asyncio
from datetime import datetime
import ipaddress
import json
from pathlib import Path
import platform
import shutil
import socket
import subprocess
import sys

CONFIG_PATH = Path(__file__).resolve().with_name('config.json')
POSITIONS = ('right', 'left', 'top', 'bottom')


def local_ipv4_addresses():
    """IPv4 addresses from the local stack. Does not resolve names."""
    if platform.system() != 'Windows':
        return []
    command = "(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object { $_.AddressState -eq 'Preferred' }).IPAddress"
    try:
        result = subprocess.run(['powershell.exe', '-NoProfile', '-Command', command],
                                check=False, capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return []
    found = []
    for line in (result.stdout or '').splitlines():
        text = line.strip()
        if not text:
            continue
        try:
            ip = ipaddress.ip_address(text)
        except ValueError:
            continue
        if ip.version == 4:
            found.append(str(ip))
    return sorted(set(found))


def get_lan_networks():
    if platform.system() != 'Windows':
        print('Automatic LAN scan is available on Windows; enter the server IP manually.')
        return []
    command = "$ErrorActionPreference='Stop'; $physical=@(Get-NetAdapter -Physical | Where-Object Status -eq 'Up' | Select-Object -ExpandProperty ifIndex); $route=Get-NetRoute -AddressFamily IPv4 -DestinationPrefix '0.0.0.0/0' | Where-Object { $_.InterfaceIndex -in $physical } | Sort-Object { $_.RouteMetric + $_.InterfaceMetric } | Select-Object -First 1; if ($route) { Get-NetIPAddress -InterfaceIndex $route.InterfaceIndex -AddressFamily IPv4 | Where-Object { $_.AddressState -eq 'Preferred' -and -not $_.SkipAsSource } | Select-Object -First 1 | Select-Object @{Name='Address';Expression={$_.IPAddress}},PrefixLength | ConvertTo-Json -Compress }"
    result = subprocess.run(['powershell.exe', '-NoProfile', '-Command', command],
                            check=True, capture_output=True, text=True, timeout=15)
    records = json.loads(result.stdout or '[]')
    if isinstance(records, dict):
        records = [records]
    networks = set()
    for record in records:
        network = ipaddress.ip_network(f"{record['Address']}/{record['PrefixLength']}", strict=False)
        # ponytail: scan at most 256 addresses per directly connected LAN; use manual IP on larger networks.
        if network.version == 4 and not network.is_loopback and network.num_addresses <= 256:
            networks.add(network)
        else:
            print(f'Skipping automatic scan of {network}; use manual IP if needed.')
    return sorted(networks)


async def probe_server(host, port):
    import link
    try:
        async with link.connect(f'ws://{host}:{port}', open_timeout=0.8,
                                 close_timeout=0.2, max_size=4096) as ws:
            await ws.send(json.dumps({'type': 'discover'}))
            reply = json.loads(await asyncio.wait_for(ws.recv(), 0.8))
            if isinstance(reply, dict) and reply.get('type') == 'server_info' and reply.get('protocol') == 'sharemouse-python':
                name = reply.get('machine')
                if isinstance(name, str) and name:
                    return {'host': host, 'machine': ''.join(c if c.isprintable() else '?' for c in name)[:80]}
    except (OSError, TimeoutError, ValueError, link.WebSocketException):
        pass  # A closed port or another service is not a ShareMouse server.
    return None


async def discover_servers(networks, port):
    if len(networks) != 1 or networks[0].num_addresses > 256:
        raise ValueError('Automatic scan requires one IPv4 LAN with at most 256 addresses. Enter the IP manually.')
    limit = asyncio.Semaphore(32)

    async def check(host):
        async with limit:
            return await probe_server(str(host), port)

    hosts = {host for network in networks if network.num_addresses <= 256 for host in network.hosts()}
    results = await asyncio.gather(*(check(host) for host in sorted(hosts)))
    return [result for result in results if result is not None]


def choose_server(port):
    print(f'Scanning the preferred LAN for ShareMouse servers on TCP {port}...')
    try:
        networks = get_lan_networks()
        found = asyncio.run(discover_servers(networks, port)) if networks else []
    except (OSError, ValueError, subprocess.SubprocessError, ImportError) as error:
        print(f'Cannot scan automatically: {error}')
        found = []
    for index, server in enumerate(found, 1):
        print(f"  {index}. {server['machine']} - {server['host']}:{port}")
    if not found:
        print('No server found. Start the server first, or enter its IP manually.')
    print('  0. Enter IP/hostname manually')
    while True:
        choice = input('Select number or type server IP/hostname: ').strip()
        if choice in ('', '0'):
            return input('Server IP/hostname: ').strip()
        if not choice.isdecimal():
            return choice
        if 1 <= int(choice) <= len(found):
            return found[int(choice) - 1]['host']
        print('Invalid selection.')


def choose_position():
    print('Client screen position: 1.Right  2.Left  3.Top  4.Bottom')
    while True:
        choice = input('Select [1-4, default 1]: ').strip() or '1'
        if choice in ('1', '2', '3', '4'):
            return POSITIONS[int(choice) - 1]
        if choice in POSITIONS:
            return choice
        print('Select 1, 2, 3 or 4.')


def validate_config(config):
    if not isinstance(config, dict) or config.get('version') != 1:
        raise ValueError('Unsupported configuration; run configure.py again.')
    if config.get('machine') != socket.gethostname():
        raise ValueError('Configuration belongs to another computer; run configure.py here.')
    if config.get('role') not in ('server', 'client'):
        raise ValueError('Role must be server or client.')
    host = config.get('host')
    if not isinstance(host, str) or not host:
        raise ValueError('Enter a local IP address.')
    import link
    allowed = host in ('0.0.0.0', '::') if config['role'] == 'server' else host.lower() == 'localhost'
    if not allowed and not link.is_local_ip(host):
        raise ValueError('Host must be a local IP address, not a public address or hostname.')
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
        addresses = local_ipv4_addresses()
        if addresses:
            print('Local IPv4 candidates (choose the LAN adapter): ' + ', '.join(addresses))
        else:
            print('Cannot list local IPv4 addresses; use ipconfig.')
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
        role = args.role
        if role is None:
            while True:
                print('\n1. SERVER - use this PC mouse/keyboard\n2. CLIENT - control this PC from server\n3. Show saved configuration\n0. Exit')
                choice = input('Select [0-3]: ').strip()
                if choice == '0':
                    return 0
                if choice == '3':
                    if args.config.exists():
                        show_config(load_config(args.config))
                    else:
                        print('No saved configuration.')
                elif choice in ('1', '2'):
                    role = 'server' if choice == '1' else 'client'
                    args.port = int(input(f'Port [{args.port}]: ').strip() or args.port)
                    break
                else:
                    print('Invalid selection.')
        host = args.host
        if host is None:
            host = '0.0.0.0' if role == 'server' else choose_server(args.port)
        config = dict(version=1, machine=socket.gethostname(), role=role,
                      host=host, port=args.port)
        if role == 'client':
            config['position'] = args.position or choose_position()
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
