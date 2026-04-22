import argparse
from common.config import load_config
from gateway.app import GatewayService
import time

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True, help='Path to config file')
    args = parser.parse_args()
    
    cfg = load_config(args.config)
    svc = GatewayService(cfg)
    svc.start()
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        svc.stop()
