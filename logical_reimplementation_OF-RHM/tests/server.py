import http.server
import socketserver
import argparse
import logging
import sys

# Setup basic logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger("server")

class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        logger.info(f"Received GET request from {self.client_address}")
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b"Hello from OF-RHM Server")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()
    
    with socketserver.TCPServer(("", args.port), Handler) as httpd:
        logger.info(f"Serving at port {args.port}")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
