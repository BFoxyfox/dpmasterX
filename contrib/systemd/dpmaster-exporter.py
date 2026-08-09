#!/usr/bin/env python3
"""Small, dependency-free public status page and Prometheus exporter."""

import html
import http.server
import json
import os
import socket
import threading
import time
from collections import OrderedDict
from urllib.parse import urlsplit

STATE = os.environ.get("DPMASTER_STATE_FILE", "/var/lib/dpmaster/servers.state")
LISTEN_ADDRESS = os.environ.get("DPMASTER_HTTP_ADDRESS", "0.0.0.0")
LISTEN_PORT = int(os.environ.get("DPMASTER_HTTP_PORT", "80"))
MAX_WORKERS = int(os.environ.get("DPMASTER_HTTP_MAX_WORKERS", "32"))
RATE = float(os.environ.get("DPMASTER_HTTP_RATE", "5"))
BURST = float(os.environ.get("DPMASTER_HTTP_BURST", "20"))
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


def active_servers():
    now = int(time.time())
    servers = []
    try:
        with open(STATE, encoding="ascii") as stream:
            next(stream, None)
            for line in stream:
                fields = line.split()
                if len(fields) != 8 or int(fields[0]) <= now:
                    continue
                expiry, family, address, port, protocol, state, game, game_type = fields
                state_name = {"2": "empty", "3": "occupied", "4": "full"}.get(state, "unknown")
                servers.append({
                    "address": address,
                    "port": int(port),
                    "family": f"IPv{family}",
                    "protocol": int(protocol),
                    "state": state_name,
                    "game": game,
                    "game_type": game_type,
                    "expires_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(expiry))),
                })
    except (OSError, ValueError):
        pass
    return servers


def metric_values(servers=None):
    if servers is None:
        servers = active_servers()
    now = int(time.time())
    try:
        newest = int(os.path.getmtime(STATE))
    except OSError:
        newest = 0
    return {
        "dpmaster_up": int(master_up()),
        "dpmaster_servers_active": len(servers),
        "dpmaster_servers_ipv4": sum(server["family"] == "IPv4" for server in servers),
        "dpmaster_servers_ipv6": sum(server["family"] == "IPv6" for server in servers),
        "dpmaster_servers_empty": sum(server["state"] == "empty" for server in servers),
        "dpmaster_servers_occupied": sum(server["state"] == "occupied" for server in servers),
        "dpmaster_servers_full": sum(server["state"] == "full" for server in servers),
        "dpmaster_state_age_seconds": max(0, now - newest) if newest else -1,
        "dpmaster_exporter_uptime_seconds": int(time.time() - STARTED),
    }


def metrics(values):
    return "".join(f"# TYPE {key} gauge\n{key} {value}\n" for key, value in values.items())


def status_page(values, servers, prefix=""):
    online = bool(values["dpmaster_up"])
    status = "Online" if online else "Unavailable"
    colour = "#39d98a" if online else "#ff5c5c"
    cards = (
        ("Active servers", values["dpmaster_servers_active"]),
        ("IPv4", values["dpmaster_servers_ipv4"]),
        ("IPv6", values["dpmaster_servers_ipv6"]),
        ("Games in progress", values["dpmaster_servers_occupied"]),
    )
    card_html = "".join(
        f'<div class="card"><strong>{html.escape(str(value))}</strong><span>{html.escape(label)}</span></div>'
        for label, value in cards
    )
    rows = "".join(
        "<tr>"
        f'<td><code>{html.escape(("[" + server["address"] + "]" if server["family"] == "IPv6" else server["address"]) + ":" + str(server["port"]))}</code></td>'
        f'<td>{html.escape(server["game"])}</td><td>{html.escape(server["game_type"])}</td>'
        f'<td>{html.escape(str(server["protocol"]))}</td><td>{html.escape(server["state"])}</td>'
        "</tr>" for server in servers
    ) or '<tr><td colspan="5" class="empty">No active servers</td></tr>'
    prefix = html.escape(prefix, quote=True)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>dpmaster — Service status</title><style>
:root{{color-scheme:dark;font-family:system-ui,sans-serif;background:#0b1020;color:#eef2ff}}
body{{max-width:1050px;margin:0 auto;padding:clamp(2rem,8vw,6rem) 1.2rem}}
header{{margin-bottom:2.5rem}}h1{{font-size:clamp(2rem,7vw,4.5rem);margin:.25rem 0}}
.status{{display:inline-flex;align-items:center;gap:.6rem;color:{colour}}}.dot{{width:.7rem;height:.7rem;border-radius:50%;background:currentColor;box-shadow:0 0 18px currentColor}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:1rem}}.card{{background:#141b31;border:1px solid #27314f;border-radius:14px;padding:1.4rem}}
.card strong{{display:block;font-size:2rem}}.card span,footer,.empty{{color:#aeb9d8}}h2{{margin-top:3rem}}
.table{{overflow:auto;border:1px solid #27314f;border-radius:14px}}table{{width:100%;border-collapse:collapse;background:#141b31}}th,td{{padding:.8rem 1rem;text-align:left;border-bottom:1px solid #27314f;white-space:nowrap}}th{{color:#aeb9d8}}a{{color:#8db4ff}}footer{{margin-top:3rem;font-size:.9rem}}
</style></head><body><header><div class="status"><i class="dot"></i>{status}</div><h1>Master server</h1><p>Public dpmaster service status.</p></header>
<main><div class="grid">{card_html}</div><h2>Active servers</h2><div class="table"><table><thead><tr><th>Address</th><th>Game</th><th>Type</th><th>Protocol</th><th>Status</th></tr></thead><tbody>{rows}</tbody></table></div></main>
<footer>Updated at {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())} · <a href="{prefix}/healthz">Health</a> · JSON: <a href="{prefix}/api/status.json">status</a> / <a href="{prefix}/api/servers.json">servers</a></footer></body></html>"""


class RateLimiter:
    """Bounded, per-address token bucket to shed abusive HTTP clients."""

    def __init__(self, rate, burst, max_clients=10000):
        self.rate, self.burst, self.max_clients = rate, burst, max_clients
        self.clients = OrderedDict()
        self.lock = threading.Lock()

    def allow(self, address):
        now = time.monotonic()
        with self.lock:
            tokens, seen = self.clients.pop(address, (self.burst, now))
            tokens = min(self.burst, tokens + (now - seen) * self.rate)
            allowed = tokens >= 1
            self.clients[address] = (tokens - 1 if allowed else tokens, now)
            if len(self.clients) > self.max_clients:
                self.clients.popitem(last=False)
            return allowed


LIMITER = RateLimiter(RATE, BURST)


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "dpmaster"
    sys_version = ""

    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def _reply(self, status, body, kind="text/plain; charset=utf-8", head=False):
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def _public_prefix(self):
        """Return a safe path prefix explicitly supplied by a trusted proxy."""
        prefix = self.headers.get("X-Forwarded-Prefix", "").strip().rstrip("/")
        parsed = urlsplit(prefix)
        if not prefix or not prefix.startswith("/") or prefix.startswith("//"):
            return ""
        if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment or parsed.path != prefix:
            return ""
        return prefix

    def _json(self, value, head=False):
        body = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()
        self._reply(200, body, "application/json; charset=utf-8", head)

    def _get(self, head=False):
        if not LIMITER.allow(self.client_address[0]):
            self.close_connection = True
            self._reply(429, b"too many requests\n", head=head)
            return
        path = urlsplit(self.path).path
        if path == "/healthz":
            healthy = master_up()
            self._reply(200 if healthy else 503, b"ok\n" if healthy else b"unavailable\n", head=head)
        elif path == "/metrics":
            body = metrics(metric_values()).encode()
            self._reply(200, body, "text/plain; version=0.0.4; charset=utf-8", head)
        elif path == "/api/status.json":
            servers = active_servers()
            values = metric_values(servers)
            self._json({
                "status": "online" if values["dpmaster_up"] else "unavailable",
                "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "servers": {
                    "active": values["dpmaster_servers_active"],
                    "ipv4": values["dpmaster_servers_ipv4"],
                    "ipv6": values["dpmaster_servers_ipv6"],
                    "empty": values["dpmaster_servers_empty"],
                    "occupied": values["dpmaster_servers_occupied"],
                    "full": values["dpmaster_servers_full"],
                },
                "state_age_seconds": values["dpmaster_state_age_seconds"],
                "exporter_uptime_seconds": values["dpmaster_exporter_uptime_seconds"],
            }, head)
        elif path == "/api/servers.json":
            servers = active_servers()
            self._json({"count": len(servers), "servers": servers}, head)
        elif path == "/":
            servers = active_servers()
            self._reply(200, status_page(metric_values(servers), servers, self._public_prefix()).encode(), "text/html; charset=utf-8", head)
        else:
            self._reply(404, b"not found\n", head=head)

    def do_GET(self):
        self._get()

    def do_HEAD(self):
        self._get(head=True)

    def log_message(self, format, *args):
        return


class BoundedHTTPServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, *args, **kwargs):
        self.worker_slots = threading.BoundedSemaphore(MAX_WORKERS)
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        if not self.worker_slots.acquire(blocking=False):
            request.close()
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.worker_slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.worker_slots.release()


if __name__ == "__main__":
    BoundedHTTPServer((LISTEN_ADDRESS, LISTEN_PORT), Handler).serve_forever()
