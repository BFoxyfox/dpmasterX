# systemd production deployment

These files provide a hardened Linux deployment for dpmaster, an HTTP health
and Prometheus exporter, and a recurring UDP probe.

## Paths and prerequisites

- dpmaster binary: `/usr/local/bin/dpmaster`
- service account: `dpmaster` with no interactive shell
- writable state directory: `/var/lib/dpmaster`
- helper scripts: `/usr/local/libexec`
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

## HTTP endpoints

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
