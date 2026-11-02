"""Run the server/client role saved by configure.py on this computer."""
import argparse
import asyncio
from pathlib import Path
import sys

from configure import CONFIG_PATH, load_config, server_uri, show_config


async def run(config):
    if config['role'] == 'server':
        from server import MouseServer
        app = MouseServer(host=config['host'], port=config['port'])
        operation = app.start
    else:
        from client import MouseClient
        app = MouseClient(server_uri=server_uri(config), position=config['position'])
        operation = app.connect
    try:
        await operation()
    finally:
        app.stop()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=CONFIG_PATH)
    parser.add_argument('--check', action='store_true', help='validate config only; no hooks or network')
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        show_config(config)
        if args.check:
            return 0
        print('Move the server mouse to the client-facing edge. Ctrl+C stops this process.')
        asyncio.run(run(config))
        return 0
    except KeyboardInterrupt:
        return 0
    except (OSError, ValueError, ImportError) as error:
        print(f'ShareMouse error: {error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
