# College Lab Setup Guide

Step-by-step instructions to set up the USB Device Monitoring and Alert System
on the college Ubuntu lab network: one machine as the **central server**, and one
or more machines as **monitored clients**.

The general run/test guide is in [`instructions.md`](instructions.md); the full
reference is [`README.md`](README.md). This file is the shortest path to a
working lab deployment.

## What you need

- One Ubuntu PC for the **server** (stays on during the exam).
- One or more Ubuntu PCs as **client** machines (the ones being monitored).
- A working LAN connection between server and clients.
- Admin/sudo rights to install packages and manage systemd services.
- A USB device for the physical detection test.
- No internet is needed at runtime, but you need internet once to install
  packages.

## Overview

| Machine | Role | Service | Unit file |
|---------|------|---------|-----------|
| Server PC | Central Flask + SQLite + dashboard | `exam-monitor-server` | `deploy/exam-monitor-server.service` |
| Each lab PC | USB monitor agent | `exam-monitor-client` | `deploy/exam-monitor-client.service` |

Code lives in `/opt/exam-monitor`, secrets in `/etc/exam-monitor`, writable
state in `/var/lib/exam-monitor`, and everything runs under the dedicated
`exam-monitor` account (no root).

---

## Part A - Set up the server

Run all of these on the PC chosen as the server.

### A1. Install system packages

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git
```

### A2. Create the service account and directories

These commands are safe to run more than once.

```bash
sudo useradd --system --home /opt/exam-monitor --shell /usr/sbin/nologin exam-monitor || true
sudo install -d -o root -g root -m 0755 /opt/exam-monitor
sudo install -d -o exam-monitor -g exam-monitor -m 0750 /var/lib/exam-monitor
sudo install -d -o root -g root -m 0750 /etc/exam-monitor
```

### A3. Copy the project into place

Replace `<repo-url>` with the actual Git URL, or copy the folder another way.

```bash
sudo git clone <repo-url> /opt/exam-monitor
```

If you already have the project folder, copy it instead, for example:

```bash
sudo rsync -a --delete "/path/to/Exam-monitor/" /opt/exam-monitor/
```

### A4. Create the virtual environment and install dependencies

```bash
cd /opt/exam-monitor
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt      # installs Flask, gunicorn, pyudev (Linux)
```

### A5. Create the server secrets file

```bash
sudo install -m 0640 -o root -g exam-monitor deploy/server.env.example /etc/exam-monitor/server.env
sudo nano /etc/exam-monitor/server.env
```

Generate three **different** strong values, one at a time:

```bash
python3 -c 'import secrets; print(secrets.token_hex(32))'
```

Fill in `server.env` with:

```ini
APP_ENV=production
DATABASE_PATH=/var/lib/exam-monitor/exam_monitor.db
HOST=0.0.0.0
PORT=5000

AUTH_TOKEN=<paste-ingestion-token>
DASHBOARD_TOKEN=<paste-different-dashboard-token>
SECRET_KEY=<paste-session-key>

STALE_AFTER_SECONDS=90
SQLITE_BUSY_TIMEOUT_MS=5000
SESSION_LIFETIME_HOURS=12
SESSION_COOKIE_SECURE=false
```

Notes:
- `AUTH_TOKEN` is for clients writing events. `DASHBOARD_TOKEN` is the
  professor's login password. They **must** be different.
- Keep these values; you will paste `AUTH_TOKEN` into every client later.
- Do not commit or share this file.

### A6. Install, enable, and start the server service

```bash
sudo install -m 0644 deploy/exam-monitor-server.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now exam-monitor-server
```

The server creates its SQLite database automatically at startup.

### A7. Verify the server

```bash
systemctl status exam-monitor-server
journalctl -u exam-monitor-server -n 50 --no-pager
curl http://127.0.0.1:5000/api/health
```

The health check should return a JSON response. If `curl` is missing:
`sudo apt install -y curl`.

### A8. Find the server's LAN IP

```bash
hostname -I
```

Use the private address on the interface the clients share (for example
`192.168.1.10`). You will enter this as `SERVER_URL` on every client.

### A9. Allow the lab subnet through the firewall

Only if a firewall is active. Replace the subnet with your lab network.

```bash
sudo ufw allow from 192.168.1.0/24 to any port 5000 proto tcp
```

Do **not** expose this port to the internet. Confirm the port with:

```bash
sudo ufw status
```

---

## Part B - Set up each client (monitored PC)

Repeat **all** of Part B on every monitored machine. Give each one a unique
`COMPUTER_ID` (`LAB-PC-01`, `LAB-PC-02`, ...).

### B1. Install system packages

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git libudev1
```

### B2. Create the service account and directories

```bash
sudo useradd --system --home /opt/exam-monitor --shell /usr/sbin/nologin exam-monitor || true
sudo install -d -o root -g root -m 0755 /opt/exam-monitor
sudo install -d -o exam-monitor -g exam-monitor -m 0750 /var/lib/exam-monitor
sudo install -d -o root -g root -m 0750 /etc/exam-monitor
```

### B3. Copy the project into place

```bash
sudo git clone <repo-url> /opt/exam-monitor
```

or, if copying a local folder:

```bash
sudo rsync -a --delete "/path/to/Exam-monitor/" /opt/exam-monitor/
```

### B4. Create the virtual environment and install dependencies

```bash
cd /opt/exam-monitor
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### B5. Create the client secrets file

```bash
sudo install -m 0640 -o root -g exam-monitor deploy/client.env.example /etc/exam-monitor/client.env
sudo nano /etc/exam-monitor/client.env
```

Fill in with this machine's values (use the **server's** LAN IP and the
**server's** `AUTH_TOKEN`):

```ini
COMPUTER_ID=LAB-PC-01
SERVER_URL=http://192.168.1.10:5000
AUTH_TOKEN=<same-as-server-AUTH_TOKEN>
CLIENT_DB_PATH=/var/lib/exam-monitor/client_outbox.db
RETRY_INTERVAL=5
REQUEST_TIMEOUT=10
HEARTBEAT_INTERVAL_SECONDS=30
```

Each machine must have a different `COMPUTER_ID`. Clients only need the
ingestion token, never the dashboard token.

### B6. Give the agent udev access

```bash
sudo usermod -aG plugdev exam-monitor
```

If USB events still are not detected after starting the service (Part B7), see
"Troubleshooting" below.

### B7. Install, enable, and start the client service

```bash
cd /opt/exam-monitor
sudo install -m 0644 deploy/exam-monitor-client.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now exam-monitor-client
```

### B8. Verify the client

```bash
systemctl status exam-monitor-client
journalctl -u exam-monitor-client -n 50 --no-pager
```

The agent should log that it started, is sending heartbeats, and is watching
udev. From the client, confirm the server is reachable:

```bash
curl http://192.168.1.10:5000/api/health
```

---

## Part C - Access the dashboard and test

### C1. Open the dashboard

From any machine on the lab LAN, open a browser:

```text
http://<server-lan-ip>:5000/
```

Sign in with the `DASHBOARD_TOKEN` from the server's `server.env`. The main page
shows monitored computers and alerts; `/events` shows the full event history
with filters.

### C2. Confirm clients are online

Within ~30 seconds of starting each client service, it should appear on the
dashboard as **Heartbeat OK**. A client that never appears, or shows **stale**,
is not sending heartbeats - check its `SERVER_URL` and `AUTH_TOKEN`.

### C3. Physical USB test (on a client PC)

1. Insert a USB storage device.
2. Refresh the dashboard; a **connected** event should appear under that
   `COMPUTER_ID`.
3. Safely remove the device; a **disconnected** event should appear.
4. Repeat with a non-storage peripheral (mouse/keyboard) if available.

The system records device metadata only; it never reads or copies files. Do not
use personal USB drives with sensitive data for testing.

### C4. Quick synthetic check (optional, no USB needed)

On a client, with its environment loaded, send one labelled test event:

```bash
cd /opt/exam-monitor && source venv/bin/activate
COMPUTER_ID=LAB-TEST SERVER_URL=http://<server-lan-ip>:5000 \
  AUTH_TOKEN=<AUTH_TOKEN> python -m client.agent --synthetic --event-type connected
```

The dashboard should show `LAB-TEST` with a `[SYNTHETIC-TEST]` event. This is
**simulated** - it proves the client-to-server path, not USB detection.

---

## Common operations

```bash
# Check both services (on the server; client service on a client)
systemctl status exam-monitor-server exam-monitor-client
journalctl -u exam-monitor-server -n 100 --no-pager
journalctl -u exam-monitor-client -f          # follow live

# Restart after a config change
sudo systemctl restart exam-monitor-server
sudo systemctl restart exam-monitor-client

# Stop monitoring after the exam
sudo systemctl stop exam-monitor-client
```

### Back up the database (server)

```bash
sudo systemctl stop exam-monitor-server
sudo cp /var/lib/exam-monitor/exam_monitor.db /var/lib/exam-monitor/exam_monitor.db.bak
sudo systemctl start exam-monitor-server
```

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `Configuration error: ... required in production` | A secret is missing in `/etc/exam-monitor/server.env`; set it and restart the server. |
| Dashboard returns `401` on login | Wrong password; use the exact `DASHBOARD_TOKEN`. |
| Dashboard unreachable from other PCs | Server `HOST` is not `0.0.0.0`, firewall blocks port 5000, or wrong LAN IP. |
| Client events never appear | Wrong `SERVER_URL` or `AUTH_TOKEN` on the client, or no udev access. |
| Client shows **stale** / never online | Check `journalctl -u exam-monitor-client`; verify `SERVER_URL` uses the server LAN IP (not `127.0.0.1`) and the token matches. |
| `database is locked` under load | Raise `SQLITE_BUSY_TIMEOUT_MS` in `server.env` and restart. |
| Service exits immediately | Read `journalctl -u <service> -n 50 --no-pager`; usually a bad path or env file. |
| USB not detected on a client | Confirm the service runs; if needed, run the client unit as root (`User=root`) or fix `plugdev` membership and reboot. |

---

## Security reminders

- Keep `AUTH_TOKEN`, `DASHBOARD_TOKEN`, and `SECRET_KEY` different and secret.
- Never commit real secrets or paste them into logs, screenshots, or reports.
- Use only the trusted, switched lab LAN; plain `http://` sends tokens in the
  clear. Add HTTPS if the threat model requires it.
- Do not expose the server port to the internet.
- A heartbeat proves the agent is alive, not that a machine is secure or
  cheating-free. USB events are device metadata, not proof of cheating.
