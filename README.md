# USB Device Monitoring and Alert System

A lightweight system for monitoring USB device connection and disconnection
events on lab computers during practical examinations.

The backend (Flask + SQLite), the USB monitoring client agent, and the
professor's web dashboard are implemented.

## What the system does

- Lab computers (Ubuntu) will run a client that detects USB connect/disconnect
  events and sends them to a central server over the local Ethernet LAN.
- The client also sends periodic **heartbeats**, so the dashboard can tell
  whether each agent is alive even when no USB device is being plugged in.
- The server (Flask + SQLite) authenticates clients, stores events, and keeps
  track of monitored computers.
- No internet access is required at runtime.
- The system records only device metadata (category, vendor/product IDs, name).
  It never reads, copies, or inspects files on the USB device.

## Project structure

```
server/
    __init__.py        # Exposes create_app()
    app.py             # Application factory, session/cookie config, error handlers
    config.py          # Reads and validates environment variables
    db.py              # SQLite schema, WAL mode, and connection helpers
    auth.py            # Ingestion token, read access, CSRF, and login decorators
    routes/
        __init__.py
        api.py         # health, heartbeat, event ingestion, and read-only APIs
        auth.py        # /login and /logout browser session routes
        dashboard.py   # HTML routes for the dashboard pages
    templates/         # Jinja templates (base, index, events, login, error)
    static/
        css/style.css
        js/common.js, dashboard.js, events.js
client/
    __init__.py
    config.py          # Client configuration and validation
    monitor.py         # pyudev USB monitoring (lazy import) + fake monitor
    outbox.py          # Persistent SQLite outbox queue
    sender.py          # HTTP delivery (events + heartbeats, stdlib urllib)
    agent.py           # Coordinates monitor, queue, delivery, and heartbeats
tests/
    test_api.py        # Backend ingestion tests (temporary database)
    test_client.py     # Client tests (mocked HTTP, fake monitor)
    test_dashboard.py  # Read API + dashboard rendering tests
    test_security.py   # Auth, config validation, and DB hardening tests
run.py                 # Development entry point (Flask dev server)
wsgi.py                # Production WSGI entry point (gunicorn/uWSGI)
deploy/
    exam-monitor-server.service  # systemd unit for the central server
    exam-monitor-client.service  # systemd unit for the client agent
    server.env.example           # /etc/exam-monitor/server.env template
    client.env.example           # /etc/exam-monitor/client.env template
docs/
    ACCEPTANCE.md                # demonstration procedure + physical USB checklist
requirements.txt
.env.example
.gitignore
README.md
```

## Requirements

- Python 3 (tested with 3.14 on macOS; any Python 3.9+ should work)
- Flask and pytest (see `requirements.txt`)

## Setup (macOS)

Create and activate a virtual environment:

```bash
python3 -m venv venv
source venv/bin/activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

> **Ubuntu difference:** use `python3 -m venv venv` and `source venv/bin/activate`
> the same way. If `venv` is unavailable, install it with
> `sudo apt install python3-venv` (this is a system change you must do once).

## Configure environment variables

The server reads configuration from environment variables. There are **two
independent secrets**: an ingestion token for client agents and a dashboard
token for the professor's read access. Generate each one separately:

```bash
export AUTH_TOKEN="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
export DASHBOARD_TOKEN="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
export SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
export APP_ENV="development"               # "production" enables strict validation
export DATABASE_PATH="./exam_monitor.db"   # optional; this is the default
export HOST="127.0.0.1"                    # use 0.0.0.0 to accept LAN traffic
export PORT="5000"                         # optional; default is 5000
export STALE_AFTER_SECONDS="90"            # optional; heartbeat online/stale threshold
```

| Variable | Required | Meaning |
|----------|----------|---------|
| `AUTH_TOKEN` | yes (prod) | Client ingestion token for `POST /api/events` and `POST /api/heartbeat`. |
| `DASHBOARD_TOKEN` | yes (prod) | Dashboard login password and bearer token for the read APIs. Must differ from `AUTH_TOKEN`. |
| `SECRET_KEY` | yes (prod) | Signs the browser session cookie. Generate a long random value. |
| `APP_ENV` | no | `development` (default) or `production`. Production fails fast on missing/weak secrets. |
| `DATABASE_PATH` | no | SQLite file. Default `./exam_monitor.db`. |
| `HOST` / `PORT` | no | Bind address. Default `127.0.0.1:5000`. |
| `STALE_AFTER_SECONDS` | no | Heartbeat online/stale threshold. Default 90. |
| `SQLITE_BUSY_TIMEOUT_MS` | no | Lock wait before "database is locked". Default 5000. |
| `SESSION_LIFETIME_HOURS` | no | Browser session lifetime. Default 12. |
| `SESSION_COOKIE_SECURE` | no | Set `true` only when serving over HTTPS. |

See `.env.example` for the full list with comments. Environment variables are
**not** loaded from a file automatically; export them in your shell (or, on
Ubuntu, put them in the systemd `EnvironmentFile`). Never commit real secrets.

> **Production validation.** With `APP_ENV=production`, the server refuses to
> start unless `AUTH_TOKEN`, `DASHBOARD_TOKEN`, and `SECRET_KEY` are all set,
> `AUTH_TOKEN` and `DASHBOARD_TOKEN` differ, and `SECRET_KEY` is at least 16
> characters. In development, the same problems are only logged as warnings so
> local work stays convenient.

## Initialise the database

```bash
python run.py init-db
```

This creates the SQLite file and its tables. It is safe to run repeatedly.
The production entry point (`wsgi.py`) runs the same step automatically at
startup, so the systemd deployment needs no separate init command.

## Run the server locally

```bash
python run.py
```

The server starts on `http://127.0.0.1:5000` by default. To accept connections
from other lab computers, set `HOST=0.0.0.0` before starting.

> **Development server only.** `python run.py` uses Flask's built-in server,
> which is fine for local development and a college demo. For a repeatable Ubuntu
> deployment, `wsgi.py` exposes the application for gunicorn behind a systemd
> unit (see "Deploy on Ubuntu with systemd" below).

## Professor's dashboard

Open the dashboard in a browser at the server's address:

- `http://127.0.0.1:5000/` — main dashboard (summary cards, monitored
  computers, recent alerts).
- `http://127.0.0.1:5000/events` — event history with filters and pagination.

The pages are server-rendered Jinja templates with local CSS and vanilla
JavaScript. There is **no CDN, no external font, and no build step**, so the
dashboard works fully offline on the lab LAN. It refreshes automatically every
5 seconds without a full-page reload and never creates fake events.

### Dashboard login

The dashboard and its read APIs are protected by a **sign-in session** so that
only the professor can view event metadata:

1. Visiting `/` or `/events` while signed out redirects to `/login`.
2. Enter the `DASHBOARD_TOKEN` value as the password. On success the server
   starts a signed session cookie and sends you to the original page.
3. "Sign out" in the header ends the session.

Login is CSRF-protected, failed attempts return a generic `401` (they never echo
the expected password), and the session is cleared and regenerated on login and
logout. The `DASHBOARD_TOKEN` is separate from the client `AUTH_TOKEN`: a
compromised dashboard password cannot be used to submit fake events, and a
compromised client token cannot be used to read the dashboard.

Read access can also be granted without a browser session by sending
`Authorization: Bearer <DASHBOARD_TOKEN>` to `GET /api/computers` or
`GET /api/events` (for scripts and monitoring).

> **HTTPS:** the session cookie is marked `HttpOnly` and `SameSite=Lax`. Set
> `SESSION_COOKIE_SECURE=true` once you serve the dashboard over HTTPS so the
> cookie is only sent over TLS.

### Access from another machine on the lab LAN

1. Start the server with `HOST=0.0.0.0` so it listens on all interfaces:
   ```bash
   export HOST=0.0.0.0 PORT=5000 AUTH_TOKEN="<token>" DASHBOARD_TOKEN="<token>"
   python run.py
   ```
2. From another computer on the same LAN, open
   `http://<server-lan-ip>:5000/` (for example `http://192.168.1.10:5000/`) and
   sign in with the dashboard token. Find the server's LAN IP with
   `ip addr` or `hostname -I` (Ubuntu) or `ipconfig getifaddr en0` (macOS). No
   internet connection is required.
3. If the server runs a firewall, allow the port on the LAN only. With `ufw`:
   ```bash
   sudo ufw allow from 192.168.1.0/24 to any port 5000 proto tcp
   ```
   Replace the subnet with your lab network. Do **not** expose the port to the
   internet.

### Online / stale / no-heartbeat status — meaning and limits

The dashboard classifies each computer from its **last heartbeat**
(`computers.last_heartbeat`), which the server updates when a client POSTs to
`/api/heartbeat`:

- **Heartbeat OK (online):** a heartbeat arrived within `STALE_AFTER_SECONDS`
  (default 90 seconds).
- **No recent heartbeat (stale):** the last heartbeat is older than the
  threshold, which usually means the client stopped or the machine is off.
- **No heartbeat yet:** the computer is registered (for example its first event
  arrived) but it has never sent a heartbeat.

USB event activity is shown separately as **Last activity** (`last_seen` /
`last_event_at`), so you can tell "the agent is alive but quiet" (fresh
heartbeat, old activity) apart from "the agent stopped" (old heartbeat).

Clients send a heartbeat every `HEARTBEAT_INTERVAL_SECONDS` (default 30), which
should stay comfortably below `STALE_AFTER_SECONDS`. Heartbeats keep the status
meaningful even during a quiet exam where no USB device is used.

**Limitation:** a heartbeat proves the agent process is running and can reach
the server. It is **not** a security guarantee and does not by itself prove any
machine is free of cheating. Treat it as liveness monitoring only.

### Event history filters

On `/events` you can filter by:

- **Computer ID** — exact match (for example `LAB-PC-01`).
- **Event type** — connected or disconnected.
- **Device category** — `usb_storage`, `usb_peripheral`, or `unknown`.
- **Search** — partial match on computer ID or device name.
- **Rows per page** — 20, 50, or 100.

Filter values are encoded in the URL (so a view can be bookmarked or shared) and
validated by the backend; invalid values return a clear error. The "Clear"
button resets all filters.

The dashboard's "View events" link on each computer opens `/events` pre-filtered
to that computer.

## Run tests

```bash
pytest
```

This runs all four suites: `tests/test_api.py` (ingestion), `tests/test_client.py`
(client), `tests/test_dashboard.py` (read APIs + dashboard rendering), and
`tests/test_security.py` (authentication, CSRF, config validation, and SQLite
hardening). All tests use temporary SQLite databases, test tokens, a fake USB
monitor, and mocked HTTP. They never touch your development database and require
no USB hardware, root privileges, a running server, or a LAN.

To run a single suite:

```bash
pytest tests/test_client.py
pytest tests/test_dashboard.py
pytest tests/test_security.py
```

## Acceptance testing and demonstration

A complete, repeatable demonstration procedure and a manual physical-USB
acceptance checklist live in [`docs/ACCEPTANCE.md`](docs/ACCEPTANCE.md). That
document also restates the honesty limits: synthetic events are simulations,
a heartbeat proves liveness only (not security), USB events are metadata only,
and no event by itself proves cheating.

## Example requests

Check health (no authentication required):

```bash
curl http://127.0.0.1:5000/api/health
```

Submit a synthetic USB event (replace `<TOKEN>` with your `AUTH_TOKEN`):

```bash
curl -X POST http://127.0.0.1:5000/api/events \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{
        "event_id": "evt-0001",
        "computer_id": "LAB-PC-01",
        "event_type": "connected",
        "device_category": "usb_storage",
        "vendor_id": "0781",
        "product_id": "5567",
        "device_name": "SanDisk Cruzer",
        "occurred_at": "2026-10-11T14:07:00Z"
      }'
```

Expected `201` response:

```json
{"status": "created", "message": "event recorded",
 "event_id": "evt-0001", "computer_id": "LAB-PC-01"}
```

Sending the same `event_id` again returns `200` with `{"status": "duplicate", ...}`
and does not create a second record.

Send a heartbeat (this is what the client does every
`HEARTBEAT_INTERVAL_SECONDS`; note it does **not** create an event):

```bash
curl -X POST http://127.0.0.1:5000/api/heartbeat \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"computer_id": "LAB-PC-01"}'
```

Expected `200` response:

```json
{"status": "ok", "message": "heartbeat recorded",
 "computer_id": "LAB-PC-01", "last_heartbeat": "2026-10-11T14:07:00Z"}
```

> **Note:** these synthetic API events test **server ingestion only**. They do
> not test actual USB detection, which is the job of the Linux client using
> udev (see the client section below).

Read the dashboard data with the dashboard token (no browser needed):

```bash
curl -H "Authorization: Bearer <DASHBOARD_TOKEN>" \
  http://127.0.0.1:5000/api/computers

curl -H "Authorization: Bearer <DASHBOARD_TOKEN>" \
  "http://127.0.0.1:5000/api/events?per_page=5"
```

Without a valid session or dashboard bearer token, both endpoints return
`401 {"error": "authentication required"}`.

## USB monitoring client

The client runs on each Ubuntu lab computer. It watches USB connect/disconnect
events with `pyudev`, writes each event to a local SQLite outbox immediately,
and delivers queued events to `POST /api/events`. If the server is unreachable,
events stay queued and are retried, so nothing is lost.

A separate **heartbeat thread** posts to `POST /api/heartbeat` on its own
interval, independently of USB activity and of event delivery. A heartbeat
failure never blocks or slows USB monitoring, and event delivery keeps retrying
even if heartbeats cannot reach the server.

The client only reads udev **metadata** (vendor/product IDs, model name, device
class). It never mounts, opens, reads, copies, or inspects files on a USB
device.

### Client configuration

The client reads the same environment-variable style as the server. See
`.env.example` for the full list.

| Variable | Required | Default | Meaning |
|----------|----------|---------|---------|
| `COMPUTER_ID` | yes | - | Unique name, e.g. `LAB-PC-01` |
| `SERVER_URL` | yes | - | Base URL, e.g. `http://192.168.1.10:5000` |
| `AUTH_TOKEN` | yes | - | Bearer token matching the server |
| `CLIENT_DB_PATH` | no | `./client_outbox.db` | Local outbox database |
| `RETRY_INTERVAL` | no | `5` | Seconds between retry cycles |
| `REQUEST_TIMEOUT` | no | `10` | HTTP timeout in seconds |
| `HEARTBEAT_INTERVAL_SECONDS` | no | `30` | Seconds between heartbeats |

`SERVER_URL` may be given with or without a trailing slash, `/api/events`, or
`/api/heartbeat`; the client normalizes it and posts to `<SERVER_URL>/api/events`
and `<SERVER_URL>/api/heartbeat`.

### Install client dependencies on Ubuntu

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt   # installs pyudev on Linux
```

`pyudev` is listed with a `sys_platform == "linux"` marker, so macOS installs
skip it automatically. On Ubuntu you may also need the udev runtime, which is
normally already present: `sudo apt install libudev1`.

### Run the client on Ubuntu

```bash
export COMPUTER_ID="LAB-PC-01"
export SERVER_URL="http://192.168.1.10:5000"
export AUTH_TOKEN="<same token as the server>"
python -m client.agent
```

Press Ctrl+C to stop. The outbox is committed after every change, so stopping
(or even killing) the client does not lose or corrupt queued events.

On Ubuntu, monitoring USB events usually requires read access to the udev
netlink socket. Run the agent as a user in the `plugdev` group, or as root via
the provided systemd unit (see "Deploy on Ubuntu with systemd" below).

### Test delivery against the existing backend (no USB needed)

`--synthetic` sends one **clearly labelled** synthetic event
(`[SYNTHETIC-TEST] ...`), sends one heartbeat, and attempts delivery. It performs
no USB access.

```bash
# 1. Start the server (in another terminal), with HOST=0.0.0.0 for remote clients
export AUTH_TOKEN="test-token" HOST=127.0.0.1 PORT=5000
python run.py

# 2. Run the client in synthetic mode
export COMPUTER_ID="LAB-PC-01" SERVER_URL="http://127.0.0.1:5000" AUTH_TOKEN="test-token"
python -m client.agent --synthetic --event-type connected
```

You can also check the result directly:

```bash
curl http://127.0.0.1:5000/api/health
```

Synthetic mode exercises the whole client path (monitor event -> outbox ->
sender -> server) but **does not test actual USB detection**. Only running the
agent on Linux with `pyudev` exercises real udev monitoring.

### Simulate events without a physical device

- `python -m client.agent --synthetic` sends one labelled test event.
- The test suite uses `SyntheticMonitor`, which replays fake `DeviceEvent`
  objects through the same code path the real monitor uses.

Both are clearly separated from real detection: synthetic events carry a
`[SYNTHETIC-TEST]` device name and the agent logs a warning.

### Limitations

- **Permissions:** reading udev events requires appropriate privileges
  (`plugdev` group or root).
- **Classification:** category detection (`usb_storage`, `usb_peripheral`,
  `unknown`) is heuristic and based on udev metadata only. Some devices report
  incomplete data and are labelled `unknown`; we deliberately do not treat every
  USB device as storage.
- **Security:** plain `http://` transmits the bearer token in cleartext on the
  LAN. Token authentication is **not** encryption. Prefer `https://` in
  production where practical. This stage does not configure TLS.

## Deploy on Ubuntu with systemd

These steps run the server under gunicorn (via `wsgi.py`) and the client agent
as managed services that restart on failure and start on boot. They use a
dedicated, unprivileged `exam-monitor` system account, keep code under
`/opt/exam-monitor`, secrets under `/etc/exam-monitor`, and writable state under
`/var/lib/exam-monitor`. The bundled unit files already encode these paths, so
matching them avoids permission surprises.

### 1. Install the project and dependencies

```bash
# Create the service account and directories (idempotent).
sudo useradd --system --home /opt/exam-monitor --shell /usr/sbin/nologin exam-monitor || true
sudo install -d -o root -g root -m 0755 /opt/exam-monitor
sudo install -d -o exam-monitor -g exam-monitor -m 0750 /var/lib/exam-monitor
sudo install -d -o root -g root -m 0750 /etc/exam-monitor

# Copy the project into /opt/exam-monitor (git clone or rsync), then:
cd /opt/exam-monitor
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt        # gunicorn + pyudev install on Linux
```

The server creates the SQLite schema automatically at startup (via `wsgi.py`),
so there is no separate init step for the service.

### 2. Create the environment files (secrets)

```bash
sudo install -m 0640 -o root -g exam-monitor deploy/server.env.example /etc/exam-monitor/server.env
sudo install -m 0640 -o root -g exam-monitor deploy/client.env.example /etc/exam-monitor/client.env
sudo nano /etc/exam-monitor/server.env
```

`server.env` must set `APP_ENV=production`, `HOST=0.0.0.0`, a strong
`AUTH_TOKEN`, a **different** `DASHBOARD_TOKEN`, a long `SECRET_KEY`, and
`DATABASE_PATH=/var/lib/exam-monitor/exam_monitor.db` (the service account owns
that directory). `client.env` must set `COMPUTER_ID`, `SERVER_URL` (the server's
LAN IP), `CLIENT_DB_PATH=/var/lib/exam-monitor/client_outbox.db`, and the same
`AUTH_TOKEN` as the server.

Generate a secret for each value (run the command separately so the values
differ):

```bash
python3 -c 'import secrets; print(secrets.token_hex(32))'
```

### 3. Install, enable, and start the services

The client needs udev access; add the service account to `plugdev`:

```bash
sudo usermod -aG plugdev exam-monitor
sudo install -m 0644 deploy/exam-monitor-server.service /etc/systemd/system/
sudo install -m 0644 deploy/exam-monitor-client.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now exam-monitor-server
sudo systemctl enable --now exam-monitor-client
```

### 4. Check status and logs

```bash
systemctl status exam-monitor-server exam-monitor-client
journalctl -u exam-monitor-server -n 50 --no-pager
journalctl -u exam-monitor-client -n 50 --no-pager
journalctl -u exam-monitor-server -f      # follow live
```

Restart or stop:

```bash
sudo systemctl restart exam-monitor-server
sudo systemctl stop exam-monitor-client
```

### 5. Smoke-test the deployment

From the server host:

```bash
curl http://127.0.0.1:5000/api/health
curl -H "Authorization: Bearer <DASHBOARD_TOKEN>" http://127.0.0.1:5000/api/computers
```

From another LAN machine, open `http://<server-lan-ip>:5000/` and sign in. Then
run the client in synthetic mode to confirm end-to-end delivery:

```bash
cd /opt/exam-monitor && source venv/bin/activate
COMPUTER_ID=LAB-TEST SERVER_URL=http://<server-lan-ip>:5000 \
  AUTH_TOKEN=<AUTH_TOKEN> python -m client.agent --synthetic
```

The dashboard should show `LAB-TEST` as **Heartbeat OK** within a few seconds.

### 6. Back up the database (optional)

Stop the server briefly, then copy the SQLite file (never copy it while the
server is writing):

```bash
sudo systemctl stop exam-monitor-server
sudo cp /var/lib/exam-monitor/exam_monitor.db /var/lib/exam-monitor/exam_monitor.db.bak
sudo systemctl start exam-monitor-server
```

### Troubleshooting

| Symptom | Likely cause / fix |
|---------|--------------------|
| `Configuration error: ... required in production` | Missing secret in `server.env`; set all three and restart. |
| Dashboard returns `401` on login | Wrong password; use the exact `DASHBOARD_TOKEN`. |
| Dashboard unreachable from other machines | `HOST` not `0.0.0.0`, firewall blocking the port, or wrong LAN IP. |
| Client events never appear | Wrong `SERVER_URL`/`AUTH_TOKEN`, or client lacks udev access (add to `plugdev`, or run the unit as root). |
| `database is locked` under load | Increase `SQLITE_BUSY_TIMEOUT_MS`; WAL mode is already enabled. |
| Service exits immediately | Check `journalctl -u <service> -n 50`; usually a bad path or environment file. |

## API summary

| Method | Path              | Auth | Success | Errors |
|--------|-------------------|------|---------|--------|
| GET    | `/api/health`     | No   | 200, 503 | - |
| POST   | `/api/events`     | Ingestion token | 201 (created), 200 (duplicate) | 400 invalid data, 401 bad/missing auth, 500 server error |
| POST   | `/api/heartbeat`  | Ingestion token | 200 | 400 invalid data, 401 bad/missing auth, 500 server error |
| GET    | `/api/computers`  | Dashboard session or bearer | 200 | 401 unauthenticated, 500 server/database error |
| GET    | `/api/events`     | Dashboard session or bearer | 200 | 400 invalid parameters, 401 unauthenticated, 500 server/database error |
| GET    | `/login`          | No   | 200 | - |
| POST   | `/login`          | CSRF | 302 | 400 missing CSRF, 401 invalid credentials |
| POST   | `/logout`         | Session + CSRF | 302 | 400 missing CSRF |

The **client ingestion token** (`AUTH_TOKEN`) protects writes only. The
**dashboard token** (`DASHBOARD_TOKEN`) protects reads, either as a login
session or as a bearer token. They are independent and must not be the same
value.

`POST /api/heartbeat` takes `{"computer_id": "..."}`, registers the computer on
first contact, and updates `computers.last_heartbeat`. It creates no events and
is safe to repeat.

`GET /api/computers` reports each computer's `status` (`recent`, `stale`, or
`never`) based on `last_heartbeat`, alongside `last_heartbeat`, `last_seen`, and
`last_event_at` for reference.

`GET /api/events` parameters: `page` (default 1), `per_page` (default 20, max
100), `computer_id`, `event_type` (`connected`/`disconnected`),
`device_category`, and `q` (search on computer ID or device name). Events are
returned newest first (`occurred_at`, then insertion order). The response
includes `events`, `page`, `per_page`, `total`, and `total_pages`.

### Security model and limits

- **Writes** (`POST /api/events`, `POST /api/heartbeat`) require the client
  `AUTH_TOKEN`.
- **Reads** (`GET /api/computers`, `GET /api/events`, and the dashboard pages)
  require the `DASHBOARD_TOKEN`, supplied either through the login session or as
  `Authorization: Bearer <DASHBOARD_TOKEN>`.
- **Login** is CSRF-protected, uses a signed `HttpOnly`/`SameSite=Lax` session
  cookie, and returns generic errors so it never confirms which secret is right.
- **Sessions** are cleared and regenerated on login and logout. Set
  `SESSION_COOKIE_SECURE=true` when serving over HTTPS.
- Responses never include tokens or configuration secrets, and the dashboard
  escapes all user-supplied values before rendering.

Reads and writes are isolated by design: holding one token does not grant the
other capability. This is a lightweight LAN deployment; it does not implement
user accounts, roles, or TLS termination itself. Because clients post over plain
`http://` by default, the ingestion token travels in cleartext on the LAN — use
a trusted, switched lab network, and add HTTPS if the threat model requires it.
As noted earlier, a fresh heartbeat proves only that the agent is alive and
reachable; it is **not** proof that a machine is free of cheating.
