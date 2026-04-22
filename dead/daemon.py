#!/usr/bin/env python3
"""DEAD daemon entry point.

Starts the Dedicated Entropy Assurance Daemon on localhost.
The daemon exposes /entropy, /entropy_int, /epoch_key, and /status.

Usage:
    python dead/daemon.py [--port PORT]

Default port: 8000.
"""
import sys
import os

_LOGICAL = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        '..', 'logical_reimplementation_OF-RHM')
sys.path.insert(0, _LOGICAL)


def main():
    import uvicorn
    from controller.dead.server import app

    port = 8000
    if '--port' in sys.argv:
        try:
            port = int(sys.argv[sys.argv.index('--port') + 1])
        except (IndexError, ValueError):
            print('Usage: python dead/daemon.py [--port PORT]', file=sys.stderr)
            sys.exit(1)

    uvicorn.run(app, host='127.0.0.1', port=port)


if __name__ == '__main__':
    main()
