#!/usr/bin/env python3
from http.server import BaseHTTPRequestHandler, HTTPServer
import json, subprocess, time

def sh(cmd):
    try:
        return subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT)
    except Exception as e:
        return str(e)

class H(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in ["/", "/status"]:
            self.send_response(404); self.end_headers(); return
        payload = {
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "ip_addr": sh(["bash","-lc","ip -br addr"]),
            "routes": sh(["bash","-lc","ip route"]),
            "nft": sh(["bash","-lc","nft list ruleset | head -n 60"]),
        }
        b = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type","application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

def main():
    HTTPServer(("0.0.0.0", 8080), H).serve_forever()

if __name__ == "__main__":
    main()
