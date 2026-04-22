#!/usr/bin/env python3
"""
Lightweight DNS resolution HTTP server for OF-RHM.
Runs inside the gateway Mininet node's network namespace.
Reads VIP state from a shared JSON file written by the controller.
"""

import json
import os
import sys
import time
from flask import Flask, request, jsonify
import logging

STATE_FILE = os.environ.get('OFRHM_STATE_FILE', '/tmp/ofrhm_vip_state.json')

app = Flask('ofrhm_dns')

# Suppress request logging
logging.getLogger('werkzeug').setLevel(logging.WARNING)


def read_state():
    """Read current VIP state from shared file."""
    try:
        with open(STATE_FILE, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {'hosts': {}, 'mutation_interval_s': 2.0, 'entropy_provider': 'unknown'}


@app.route('/dns_resolve')
def dns_resolve():
    name = request.args.get('name', '')
    state = read_state()
    host = state.get('hosts', {}).get(name)
    if not host or not host.get('current_vip'):
        return jsonify({'error': f'Unknown host: {name}'}), 404
    return jsonify({
        'vip': host['current_vip'],
        'name': name,
        'ttl': int(state.get('mutation_interval_s', 2)),
    })


@app.route('/hosts')
def list_hosts():
    state = read_state()
    return jsonify(state.get('hosts', {}))


@app.route('/status')
def status():
    state = read_state()
    return jsonify({
        'status': 'ok',
        'entropy_provider': state.get('entropy_provider', 'unknown'),
        'mutation_interval_s': state.get('mutation_interval_s', 0),
        'hosts': len(state.get('hosts', {})),
    })


if __name__ == '__main__':
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    app.run(host='0.0.0.0', port=port)
