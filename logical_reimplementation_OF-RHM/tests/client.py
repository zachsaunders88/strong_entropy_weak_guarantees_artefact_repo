import requests
import argparse
import time
import sys

def run_client(url, count, delay):
    print(f"Connecting to {url}...")
    for i in range(count):
        try:
            start = time.time()
            resp = requests.get(url, timeout=5)
            duration = time.time() - start
            print(f"Request {i+1}: Status {resp.status_code}, Time {duration:.3f}s, Content: {resp.text.strip()}")
        except Exception as e:
            print(f"Request {i+1}: Failed - {e}")
        
        if i < count - 1:
            time.sleep(delay)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('url', help='Target URL (e.g. http://10.0.1.5:80)')
    parser.add_argument('--count', type=int, default=1)
    parser.add_argument('--delay', type=float, default=1.0)
    args = parser.parse_args()
    
    run_client(args.url, args.count, args.delay)
