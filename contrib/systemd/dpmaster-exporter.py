#!/usr/bin/env python3
"""Small, dependency-free public status page and Prometheus exporter."""

import html
import http.server
import json
import os
import re
import shlex
import socket
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

STATE = os.environ.get("DPMASTER_STATE_FILE", "/var/lib/dpmaster/servers.state")
LISTEN_ADDRESS = os.environ.get("DPMASTER_HTTP_ADDRESS", "0.0.0.0")
LISTEN_PORT = int(os.environ.get("DPMASTER_HTTP_PORT", "80"))
PUBLIC_PREFIX = os.environ.get("DPMASTER_HTTP_PREFIX", "").strip().rstrip("/")
MAX_WORKERS = int(os.environ.get("DPMASTER_HTTP_MAX_WORKERS", "32"))
RATE = float(os.environ.get("DPMASTER_HTTP_RATE", "5"))
BURST = float(os.environ.get("DPMASTER_HTTP_BURST", "20"))
QUERY_TIMEOUT = float(os.environ.get("DPMASTER_QUERY_TIMEOUT", "0.8"))
QUERY_CACHE_TTL = float(os.environ.get("DPMASTER_QUERY_CACHE_TTL", "60"))
STARTED = time.time()

# dpmaster accepts arbitrary game identifiers. This catalog covers identifiers
# built into dpmaster plus well-known games in the compatible engine ecosystem.
GAME_CATALOG = {
    "Quake3Arena": "Quake III Arena",
    "wolfmp": "Return to Castle Wolfenstein",
    "et": "Wolfenstein: Enemy Territory",
    "Nexuiz": "Nexuiz Classic",
    "Xonotic": "Xonotic",
    "Transfusion": "Transfusion",
    "Warsow": "Warsow",
    "Warfork": "Warfork",
    "Tremulous": "Tremulous",
    "Unvanquished": "Unvanquished",
    "OpenArena": "OpenArena",
    "q3ut4": "Urban Terror",
    "SmokinGuns": "Smokin' Guns",
    "WorldOfPadman": "World of Padman",
    "Reaction": "Reaction",
    "Daemon": "Unvanquished (Daemon engine)",
}
STATUS_CACHE = {}
STATUS_CACHE_LOCK = threading.Lock()
COLOUR_CODE = re.compile(r"\^.")


def game_details(identifier):
    return {
        "id": identifier,
        "name": GAME_CATALOG.get(identifier, identifier),
        "known": identifier in GAME_CATALOG,
    }


def clean_q3_text(value):
    return COLOUR_CODE.sub("", value).strip()


def parse_infostring(value):
    fields = value.lstrip("\\").split("\\")
    return {fields[index]: fields[index + 1] for index in range(0, len(fields) - 1, 2)}


def query_server(server):
    family = socket.AF_INET6 if server["family"] == "IPv6" else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_DGRAM)
    sock.settimeout(QUERY_TIMEOUT)
    try:
        sock.connect((server["address"], server["port"]))
        sock.send(b"\xff\xff\xff\xffgetstatus")
        payload = sock.recv(65535)
        if not payload.startswith(b"\xff\xff\xff\xffstatusResponse\n"):
            return {"query_ok": False, "cvars": {}, "players": []}
        lines = payload[4:].decode("latin-1", "replace").splitlines()
        cvars = parse_infostring(lines[1] if len(lines) > 1 else "")
        players = []
        for line in lines[2:]:
            try:
                values = shlex.split(line)
                if len(values) >= 3:
                    players.append({"score": int(values[0]), "ping": int(values[1]), "name": clean_q3_text(" ".join(values[2:]))})
            except (ValueError, IndexError):
                continue
        return {"query_ok": True, "cvars": cvars, "players": players}
    except OSError:
        return {"query_ok": False, "cvars": {}, "players": []}
    finally:
        sock.close()


def detected_game(server, cvars):
    evidence = " ".join((
        server["game"], cvars.get("gamename", ""), cvars.get("fs_game", ""),
        cvars.get("version", ""), cvars.get("mapname", ""), cvars.get("sv_dlURL", ""),
    )).lower()
    if "urban terror" in evidence or "q3urt" in evidence or re.search(r"(^|[^a-z])urt(?:4|[^a-z])", evidence) or "ut4_" in evidence:
        return {"id": "q3ut4", "name": "Urban Terror", "known": True, "detected_from_status": True}
    status_identifier = cvars.get("gamename") or server["game"]
    result = game_details(status_identifier)
    result["detected_from_status"] = status_identifier != server["game"]
    return result


def enrich_servers(servers):
    now = time.monotonic()
    pending = []
    results = {}
    with STATUS_CACHE_LOCK:
        for server in servers:
            key = (server["family"], server["address"], server["port"])
            cached = STATUS_CACHE.get(key)
            if cached and now - cached[0] < QUERY_CACHE_TTL:
                results[key] = cached[1]
            else:
                pending.append((key, server))
    if pending:
        with ThreadPoolExecutor(max_workers=min(8, len(pending))) as pool:
            queried = list(pool.map(lambda item: query_server(item[1]), pending))
        with STATUS_CACHE_LOCK:
            for (key, _), result in zip(pending, queried):
                STATUS_CACHE[key] = (now, result)
                results[key] = result
            if len(STATUS_CACHE) > 10000:
                STATUS_CACHE.clear()
    for server in servers:
        key = (server["family"], server["address"], server["port"])
        details = results.get(key, {"query_ok": False, "cvars": {}, "players": []})
        server.update(details)
        detected = detected_game(server, details["cvars"])
        server["game_name"] = detected["name"]
        server["game_detected_id"] = detected["id"]
        server["game_known"] = detected["known"]
        server["game_detected_from_status"] = detected["detected_from_status"]
        server["hostname"] = clean_q3_text(details["cvars"].get("sv_hostname", ""))
        server["map"] = details["cvars"].get("mapname", "")
        server["max_clients"] = int(details["cvars"].get("sv_maxclients", "0") or 0) if details["cvars"].get("sv_maxclients", "0").isdigit() else 0
        server["player_count"] = len(details["players"])
        game_type = details["cvars"].get("g_gametype", server["game_type"])
        urban_terror_modes = {
            "0": "Free For All", "3": "Team Deathmatch", "4": "Team Survivor",
            "5": "Follow the Leader", "6": "Capture and Hold", "7": "Capture the Flag",
            "8": "Bomb Mode", "9": "Jump", "10": "Freeze Tag",
        }
        server["game_mode"] = urban_terror_modes.get(game_type, game_type) if detected["id"] == "q3ut4" else game_type
    return servers


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
                decoded_game = game_details(game)
                servers.append({
                    "address": address,
                    "port": int(port),
                    "family": f"IPv{family}",
                    "protocol": int(protocol),
                    "state": state_name,
                    "game": game,
                    "game_name": decoded_game["name"],
                    "game_known": decoded_game["known"],
                    "game_type": game_type,
                    "expires_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(expiry))),
                })
    except (OSError, ValueError):
        pass
    return enrich_servers(servers)


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
        f'<td>{html.escape(server["hostname"] or "Unnamed server")}</td>'
        f'<td>{html.escape(server["game_name"])}<small><code>{html.escape(server["game"])}</code></small></td>'
        f'<td>{html.escape(server["map"] or "—")}</td><td>{html.escape(server["game_mode"] or "—")}</td>'
        f'<td>{server["player_count"]}/{server["max_clients"] or "?"}</td><td>{html.escape(server["state"])}</td>'
        "</tr>" for server in servers
    ) or '<tr><td colspan="7" class="empty">No active servers</td></tr>'
    details_html = "".join(
        f'<details><summary>{html.escape(server["hostname"] or server["address"])} — '
        f'{html.escape(server["address"])}:{server["port"]} ({len(server["cvars"])} cvars)</summary>'
        '<div class="vars"><table><tbody>' + "".join(
            f'<tr><th><code>{html.escape(key)}</code></th><td><code>{html.escape(value)}</code></td></tr>'
            for key, value in sorted(server["cvars"].items(), key=lambda item: item[0].casefold())
        ) + '</tbody></table></div></details>'
        for server in servers
    )
    prefix = html.escape(prefix, quote=True)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>dpmaster — Service status</title><style>
:root{{color-scheme:dark;font-family:system-ui,sans-serif;background:#0b1020;color:#eef2ff}}
body{{max-width:1050px;margin:0 auto;padding:clamp(2rem,8vw,6rem) 1.2rem}}
header{{margin-bottom:2.5rem}}h1{{font-size:clamp(2rem,7vw,4.5rem);margin:.25rem 0}}
.status{{display:inline-flex;align-items:center;gap:.6rem;color:{colour}}}.dot{{width:.7rem;height:.7rem;border-radius:50%;background:currentColor;box-shadow:0 0 18px currentColor}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:1rem}}.card{{background:#141b31;border:1px solid #27314f;border-radius:14px;padding:1.4rem}}
.card strong{{display:block;font-size:2rem}}.card span,footer,.empty,small{{color:#aeb9d8}}small{{display:block;margin-top:.2rem}}h2{{margin-top:3rem}}
.table,.vars{{overflow:auto;border:1px solid #27314f;border-radius:14px}}table{{width:100%;border-collapse:collapse;background:#141b31}}th,td{{padding:.8rem 1rem;text-align:left;border-bottom:1px solid #27314f;white-space:nowrap}}th{{color:#aeb9d8}}a{{color:#8db4ff}}details{{margin:.7rem 0;background:#141b31;border:1px solid #27314f;border-radius:10px}}summary{{cursor:pointer;padding:1rem}}.vars{{border:0;border-top:1px solid #27314f;border-radius:0}}.vars td{{white-space:normal;word-break:break-all}}footer{{margin-top:3rem;font-size:.9rem}}
</style></head><body><header><div class="status"><i class="dot"></i>{status}</div><h1>Master server</h1><p>Public dpmaster service status.</p></header>
<main><div class="grid">{card_html}</div><h2>Active servers</h2><div class="table"><table><thead><tr><th>Address</th><th>Name</th><th>Game</th><th>Map</th><th>Mode</th><th>Players</th><th>Status</th></tr></thead><tbody>{rows}</tbody></table></div><h2>Server details</h2>{details_html}</main>
<footer>Updated at {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())} · <a href="{prefix}/healthz">Health</a> · JSON: <a href="{prefix}/api/status.json">status</a> / <a href="{prefix}/api/servers.json">servers</a> / <a href="{prefix}/api/games.json">game catalog</a></footer></body></html>"""


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
        prefix = self.headers.get("X-Forwarded-Prefix", PUBLIC_PREFIX).strip().rstrip("/")
        parsed = urlsplit(prefix)
        if not prefix or not prefix.startswith("/") or prefix.startswith("//"):
            return ""
        if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment or parsed.path != prefix:
            return ""
        return prefix

    def _route_path(self):
        path = urlsplit(self.path).path
        prefix = self._public_prefix()
        if prefix and (path == prefix or path.startswith(prefix + "/")):
            path = path[len(prefix):] or "/"
        return path

    def _json(self, value, head=False):
        body = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()
        self._reply(200, body, "application/json; charset=utf-8", head)

    def _get(self, head=False):
        if not LIMITER.allow(self.client_address[0]):
            self.close_connection = True
            self._reply(429, b"too many requests\n", head=head)
            return
        path = self._route_path()
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
        elif path == "/api/games.json":
            observed_servers = active_servers()
            observed = sorted(
                {server["game"] for server in observed_servers}
                | {server["game_detected_id"] for server in observed_servers}
            )
            catalog = [
                {**game_details(identifier), "observed": identifier in observed}
                for identifier in sorted(set(GAME_CATALOG) | set(observed), key=str.casefold)
            ]
            self._json({
                "note": "dpmaster accepts arbitrary game identifiers; this catalog cannot be universally exhaustive.",
                "count": len(catalog),
                "games": catalog,
            }, head)
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
