#!/usr/bin/env python3
"""Small, dependency-free public status page and Prometheus exporter."""

import html
import http.server
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


def metric_values():
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
    return {
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


def metrics(values):
    return "".join(f"# TYPE {key} gauge\n{key} {value}\n" for key, value in values.items())


def status_page(values):
    online = bool(values["dpmaster_up"])
    status = "En ligne" if online else "Indisponible"
    colour = "#39d98a" if online else "#ff5c5c"
    cards = (
        ("Serveurs actifs", values["dpmaster_servers_active"]),
        ("IPv4", values["dpmaster_servers_ipv4"]),
        ("IPv6", values["dpmaster_servers_ipv6"]),
        ("Parties en cours", values["dpmaster_servers_occupied"]),
    )
    card_html = "".join(
        f'<div class="card"><strong>{html.escape(str(value))}</strong><span>{html.escape(label)}</span></div>'
        for label, value in cards
    )
    return f"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>dpmaster — État du service</title><style>
:root{{color-scheme:dark;font-family:system-ui,sans-serif;background:#0b1020;color:#eef2ff}}
body{{max-width:900px;margin:0 auto;padding:clamp(2rem,8vw,6rem) 1.2rem}}
header{{margin-bottom:2.5rem}}h1{{font-size:clamp(2rem,7vw,4.5rem);margin:.25rem 0}}
.status{{display:inline-flex;align-items:center;gap:.6rem;color:{colour}}}.dot{{width:.7rem;height:.7rem;border-radius:50%;background:currentColor;box-shadow:0 0 18px currentColor}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:1rem}}.card{{background:#141b31;border:1px solid #27314f;border-radius:14px;padding:1.4rem}}
.card strong{{display:block;font-size:2rem}}.card span,footer{{color:#aeb9d8}}footer{{margin-top:3rem;font-size:.9rem}}
</style></head><body><header><div class="status"><i class="dot"></i>{status}</div><h1>Serveur maître</h1><p>État public du service dpmaster.</p></header>
<main class="grid">{card_html}</main><footer>Actualisé à {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())} · <a href="/healthz">Santé</a></footer></body></html>"""


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
        elif path == "/":
            self._reply(200, status_page(metric_values()).encode(), "text/html; charset=utf-8", head)
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
