import json
from http.server import BaseHTTPRequestHandler

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        response = {
            "status": "ok",
            "name": "AgentTrace Control Plane API",
            "version": "1.0.0",
            "docs": "/docs"
        }
        self.wfile.write(json.dumps(response).encode('utf-8'))
