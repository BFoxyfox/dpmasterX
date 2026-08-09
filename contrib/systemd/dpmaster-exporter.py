#!/usr/bin/env python3
import http.server
import os
import socket
import time

STATE = "/var/lib/dpmaster/servers.state"
STARTED = time.time()

def master_up():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(1)
    try:
        sock.sendto(b"\xff\xff\xff\xffgetservers 68 full empty", ("127.0.0.1", 27950))
        data, _ = sock.recvfrom(2048)
        return data.startswith(b"\xff\xff\xff\xffgetserversResponse")
    except OSError:
        return False
    finally:
        sock.close()

def metrics():
    now = int(time.time())
    active = ipv4 = ipv6 = empty = occupied = full = 0
    newest = 0
    try:
        newest = int(os.path.getmtime(STATE))
        with open(STATE, encoding="ascii") as stream:
            next(stream, None)
            for line in stream:
                fields = line.split()
                if len(fields) != 8 or int(fields[0]) <= now:
                    continue
                active += 1
                ipv4 += fields[1] == "4"
                ipv6 += fields[1] == "6"
                empty += fields[5] == "2"
                occupied += fields[5] == "3"
                full += fields[5] == "4"
    except (OSError, ValueError):
        pass
    values = {
        "dpmaster_up": int(master_up()),
        "dpmaster_servers_active": active,
        "dpmaster_servers_ipv4": ipv4,
        "dpmaster_servers_ipv6": ipv6,
        "dpmaster_servers_empty": empty,
        "dpmaster_servers_occupied": occupied,
        "dpmaster_servers_full": full,
        "dpmaster_state_age_seconds": max(0, now - newest) if newest else -1,
        "dpmaster_exporter_uptime_seconds": int(time.time() - STARTED),
    }
    return "".join(f"# TYPE {key} gauge\n{key} {value}\n" for key, value in values.items())

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/healthz":
            healthy = master_up()
            body, status, kind = (b"ok\n" if healthy else b"unavailable\n"), (200 if healthy else 503), "text/plain"
        elif self.path == "/metrics":
            body, status, kind = metrics().encode(), 200, "text/plain; version=0.0.4"
        else:
            body, status, kind = b"not found\n", 404, "text/plain"
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, format, *args):
        return

http.server.ThreadingHTTPServer(("0.0.0.0", 80), Handler).serve_forever()
