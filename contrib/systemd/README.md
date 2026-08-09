# systemd production deployment

These files provide a hardened Linux deployment for dpmaster, an HTTP health
and Prometheus exporter, and a recurring UDP probe.

## Paths and prerequisites

- dpmaster binary: `/usr/local/bin/dpmaster`
- service account: `dpmaster` with no interactive shell
- writable state directory: `/var/lib/dpmaster`
- helper scripts: `/usr/local/libexec`
- exporter configuration: `/etc/default/dpmaster-exporter`
- UDP master port: `27950`
- TCP monitoring port: `80`

Create the account and install the files with ownership appropriate for your
distribution. The state directory must be owned by `dpmaster`; service files
belong in `/etc/systemd/system`. Then run:

```sh
systemctl daemon-reload
systemctl enable --now dpmaster.service
systemctl enable --now dpmaster-exporter.service dpmaster-health.timer
```

`dpmaster.service` uses `Type=exec` and an `ExecStartPost` UDP protocol probe.
A failed probe makes the start operation fail. It grants no capabilities.

The exporter runs separately so the C daemon does not parse HTTP or acquire
privileges for port 80. Only the exporter receives `CAP_NET_BIND_SERVICE`; its
filesystem and process access are restricted by systemd.

## Changing the HTTP port

Install `dpmaster-exporter.conf` as `/etc/default/dpmaster-exporter`, then set:

```ini
DPMASTER_HTTP_PORT=8080
```

Apply the change with:

```sh
systemctl restart dpmaster-exporter.service
```

Ports below 1024 work because the unit grants `CAP_NET_BIND_SERVICE`. Remember
to allow the selected TCP port in the host and provider firewalls. The listen
address can similarly be changed with `DPMASTER_HTTP_ADDRESS`.

## HTTP endpoints

### `GET /`

Public responsive status page showing service availability and aggregate
server counts. Responses include restrictive browser security headers.

### `GET /healthz`

Sends a local UDP `getservers` request. Returns `200 ok` when the response has a
valid dpmaster header, otherwise `503 unavailable`.

### `GET /metrics`

Exposes these Prometheus gauges:

- `dpmaster_up`
- `dpmaster_servers_active`
- `dpmaster_servers_ipv4`
- `dpmaster_servers_ipv6`
- `dpmaster_servers_empty`
- `dpmaster_servers_occupied`
- `dpmaster_servers_full`
- `dpmaster_state_age_seconds`
- `dpmaster_exporter_uptime_seconds`

The server counts come from the persisted snapshot and exclude expired rows.
`dpmaster_up` is based on a live UDP request.

## HTTP abuse and DDoS protection

The exporter accepts at most 32 simultaneous requests, applies a five-second
socket timeout, and limits each source IP to a burst of 20 requests followed by
5 requests/second. Its systemd unit also caps tasks, memory, and open files.
These defaults can be changed with `DPMASTER_HTTP_MAX_WORKERS`,
`DPMASTER_HTTP_RATE`, and `DPMASTER_HTTP_BURST` environment variables.

These controls protect the process from slow or noisy clients, but cannot stop
an attack that saturates the host's network link. For a public deployment,
place TCP/80 behind a provider with L3/L4 DDoS mitigation (or a reverse proxy/CDN
whose proxy IP ranges are enforced at the firewall). Keep UDP/27950 under the
hosting provider's game/UDP protection. Do not trust forwarded client-address
headers unless the direct connection is restricted to that proxy.

## Operations

```sh
curl -fsS http://127.0.0.1/healthz
curl -fsS http://127.0.0.1/metrics
systemctl list-timers dpmaster-health.timer
journalctl -u dpmaster -u dpmaster-exporter
systemctl --failed
```

The timer records failures but deliberately does not restart dpmaster merely
because its server list is empty.
