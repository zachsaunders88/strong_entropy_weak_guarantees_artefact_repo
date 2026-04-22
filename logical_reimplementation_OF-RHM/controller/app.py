import threading
import time
from flask import Flask, request, jsonify
from typing import List

from common.config import load_config, AppConfig
from common.logger import setup_logger
from controller.vip_allocator import HostRecord, VIPPool
from controller.logic import ControllerLogic
from controller.mutation import MutationScheduler

app = Flask(__name__)
logger = setup_logger("controller_app")

# Globals
logic: ControllerLogic = None
scheduler: MutationScheduler = None
config: AppConfig = None

def init_controller(cfg_path: str):
    global logic, scheduler, config
    config = load_config(cfg_path)
    
    # Initialize Hosts
    hosts = []
    for i in range(config.controller.hosts.count):
        # Generate IPs in the subnet
        # Assuming /24 for simplicity in generation
        # 10.0.0.1, .2, etc.
        base_ip = config.controller.hosts.subnet.split('/')[0]
        prefix = ".".join(base_ip.split('.')[:3])
        real_ip = f"{prefix}.{i+1}"
        
        host = HostRecord(
            host_id=f"h{i+1}",
            name=f"host-{i+1}",
            real_ip=real_ip,
            subnet_id="s1",
            mutation_interval_s=config.controller.vip.mutation_interval
        )
        hosts.append(host)
    
    # Initialize Entropy Provider
    entropy_source = config.controller.vip.entropy_source
    if entropy_source == "dead":
        from controller.vip_allocator import DeadEntropyProvider
        entropy_provider = DeadEntropyProvider(server_url=config.controller.vip.dead_server_url)
        logger.info(f"Using DEAD entropy provider at {config.controller.vip.dead_server_url}")
    elif entropy_source == "secrets":
        from controller.vip_allocator import SecretsProvider
        entropy_provider = SecretsProvider()
        logger.info("Using SECRETS entropy provider (secure)")
    else:
        from controller.vip_allocator import StandardRandomProvider
        entropy_provider = StandardRandomProvider()
        logger.info("Using STANDARD RANDOM entropy provider (insecure)")

    # Initialize VIPPool
    vip_pool = VIPPool(
        pool_cidr=config.controller.vip.pool_cidr,
        reuse_timeout_s=60, # Default or from config
        entropy_provider=entropy_provider
    )
    
    # Assign initial vIPs
    for host in hosts:
        try:
            vip_pool.assign_initial_vip(host)
            logger.info(f"Assigned initial vIP {host.current_vip} to {host.name}")
        except Exception as e:
            logger.error(f"Failed to assign initial vIP to {host.name}: {e}")

    # Initialize Logic and Scheduler
    logic = ControllerLogic(vip_pool, hosts)
    scheduler = MutationScheduler(vip_pool, hosts)
    scheduler.start()

@app.route('/packet_in', methods=['POST'])
def packet_in():
    data = request.json
    src_ip = data.get('src_ip')
    dst_ip = data.get('dst_ip')
    
    if not src_ip or not dst_ip:
        return jsonify({"error": "Missing src_ip or dst_ip"}), 400
        
    actions = logic.handle_packet_in(src_ip, dst_ip)
    
    return jsonify({
        "allow": actions.allow,
        "inbound_dst_rewrite": actions.inbound_dst_rewrite,
        "outbound_src_rewrite": actions.outbound_src_rewrite
    })

@app.route('/dns_resolve', methods=['GET'])
def dns_resolve():
    hostname = request.args.get('name')
    if not hostname:
        return jsonify({"error": "Missing name parameter"}), 400
        
    vip = logic.resolve_dns(hostname)
    if vip:
        return jsonify({"ip": vip, "ttl": 5}) # Short TTL
    else:
        return jsonify({"error": "Host not found"}), 404

@app.route('/state', methods=['GET'])
def get_state():
    hosts_data = []
    for host in logic.hosts_by_id.values():
        hosts_data.append({
            "name": host.name,
            "real_ip": host.real_ip,
            "current_vip": host.current_vip
        })
    return jsonify({"hosts": hosts_data})

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True, help='Path to config file')
    args = parser.parse_args()
    
    init_controller(args.config)
    app.run(port=config.controller.port, debug=False, use_reloader=False)
