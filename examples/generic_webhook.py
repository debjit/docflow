"""
Example lightweight HTTP webhook listener for triggering DocFlow on merge events.
"""

import subprocess
from http.server import HTTPServer, BaseHTTPRequestHandler
import json


class WebhookHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        try:
            payload = json.loads(body.decode("utf-8"))
            print(f"[Webhook Received] Event from repository: {payload.get('repository', {}).get('full_name')}")

            # Execute docflow generate
            cmd = ["docflow", "generate", "--repo", ".", "--docs", "./docs-repo"]
            subprocess.run(cmd, check=True)

            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"status": "docflow triggered successfully"}')
        except Exception as e:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(f'{{"error": "{str(e)}"}}'.encode("utf-8"))


def run(server_class=HTTPServer, handler_class=WebhookHandler, port=9000):
    server_address = ("", port)
    httpd = server_class(server_address, handler_class)
    print(f"DocFlow Webhook Listener running on port {port}...")
    httpd.serve_forever()


if __name__ == "__main__":
    run()
