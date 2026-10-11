# Acceptance Testing and Demonstration

This document is the Stage 6 acceptance artifact for the USB Device Monitoring
and Alert System. It contains:

1. [Honesty notes](#1-honesty-notes) — what the system does and does not prove.
2. [Automated verification](#2-automated-verification) — commands and expected results.
3. [Step-by-step demonstration](#3-step-by-step-demonstration) — a repeatable
   procedure using only documented options.
4. [Physical USB acceptance checklist](#4-physical-usb-acceptance-checklist) —
   manual tests that require a real Ubuntu lab PC and USB hardware.

Everything below was written by inspecting the actual source; no command or
option is invented.

---

## 1. Honesty notes

- **Synthetic events are simulations.** `python3 -m client.agent --synthetic`
  creates an event labelled `[SYNTHETIC-TEST] ...` and never touches USB
  hardware. It must never be presented as evidence of a real physical USB
  connection.
- **A heartbeat is liveness only.** A recent heartbeat proves the client
  process is running and can reach the server. It is **not** a security
  guarantee and does not prove a machine is free of cheating.
- **USB events are metadata only.** The system records device category,
  vendor/product IDs, and a device name. It never mounts, opens, reads,
  copies, or inspects files on a USB device.
- **An event does not prove cheating.** It records that a device was connected
  or disconnected; it is evidence for a human to interpret, not proof.

---

## 2. Automated verification

Run the full test suite (all four suites; no hardware, root, LAN, or running
server required):

```bash
python3 -m pytest -q
```

Expected: all tests pass (133 at the end of Stage 5; a local Stage 6 run
reported `133 passed`).

Static checks (nothing to configure — there is no linter config in this repo):

```bash
python3 -m compileall -q client server tests run.py wsgi.py
for f in server/static/js/*.js; do node --check "$f"; done   # if node is installed
```

During Stage 6 a local integration harness additionally exercised a real
`python3 run.py` server on a temporary database plus the real client sender
over HTTP; all 66 integration checks passed. The harness is not committed
(it is a development/acceptance tool). The procedure below reproduces the same
behaviour manually with documented commands.

---

## 3. Step-by-step demonstration

The demonstration runs on a single machine (macOS or Ubuntu) against
`127.0.0.1` using synthetic events. It takes a few minutes and needs no USB
hardware.

### 0. Prepare a clean demo workspace

```bash
cd /path/to/Exam-monitor
export DEMO_DIR="$(mktemp -d)"
export DATABASE_PATH="$DEMO_DIR/server.db"
export CLIENT_DB_PATH="$DEMO_DIR/client_outbox.db"
```

Use one `CLIENT_DB_PATH` for the whole demo so the outbox persists between
runs (this is what makes the outage step work).

### 1. Start the server

Generate three independent secrets (run each command separately so the values
differ) and export them:

```bash
export AUTH_TOKEN="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
export DASHBOARD_TOKEN="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
export SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"

export APP_ENV=development          # default; production enforces stricter checks
export HOST=127.0.0.1
export PORT=5000

python3 run.py
```

Leave this terminal running. You should see `Database ready at: ...` and
`Starting development server on http://127.0.0.1:5000`.

> For another machine on the LAN to reach it, set `HOST=0.0.0.0` and open the
> firewall for your lab subnet (see the README).

### 2. Check the health endpoint

In a second terminal:

```bash
curl -s http://127.0.0.1:5000/api/health
```

Expected: `{"status":"ok","database":"ok"}` (HTTP 200, no authentication).

### 3. Log in to the professor dashboard

Open `http://127.0.0.1:5000/` in a browser. It redirects to the login page.
Enter the value of `DASHBOARD_TOKEN` as the password and sign in. The dashboard
loads the summary cards, "Monitored Computers", and "Recent Alerts".

To print the token from the server terminal (for convenience in a demo only —
never do this in production logs):

```bash
echo "$DASHBOARD_TOKEN"
```

### 4. Start a synthetic client with a unique computer ID

In the second terminal:

```bash
export COMPUTER_ID="LAB-DEMO-01"
export SERVER_URL="http://127.0.0.1:5000"
# AUTH_TOKEN and CLIENT_DB_PATH are already exported above

python3 -m client.agent --synthetic
```

Expected output (identifiers and timestamps vary):

```
... WARNING client.agent: SYNTHETIC event: this is a test, not a real USB detection
... INFO client.agent: Heartbeat delivered for LAB-DEMO-01 (heartbeat accepted)
... INFO client.agent: Queued connected event <uuid> ([SYNTHETIC-TEST] TEST DEVICE)
... INFO client.agent: Delivered event <uuid> (created)
Heartbeat: delivered
Delivered: 1, still pending: 0
```

The process exits 0 because nothing is left pending.

### 5. Generate a synthetic disconnection event

```bash
python3 -m client.agent --synthetic --event-type disconnected
```

This queues and delivers a second, clearly labelled synthetic event.

### 6. Confirm the computer and event appear in the dashboard

Refresh `http://127.0.0.1:5000/`. `LAB-DEMO-01` appears in "Monitored
Computers" with `Heartbeat OK` and an event count of 2, and the two
`[SYNTHETIC-TEST]` events appear under "Recent Alerts". Open **Event History**
to see them with filters and pagination.

You can also read the data directly with the dashboard token:

```bash
curl -s -H "Authorization: Bearer $DASHBOARD_TOKEN" \
  http://127.0.0.1:5000/api/computers
curl -s -H "Authorization: Bearer $DASHBOARD_TOKEN" \
  "http://127.0.0.1:5000/api/events?per_page=5"
```

### 7. Demonstrate heartbeat freshness without USB activity

Each `--synthetic` run also sends one heartbeat and never touches USB. Run it
again and watch the "Last heartbeat" column update:

```bash
python3 -m client.agent --synthetic            # queues another event too
```

To show heartbeat freshness on its own (repeated beats with no USB activity),
run the real agent on an Ubuntu machine for ~90 seconds and watch the status
stay `Heartbeat OK` (see the physical checklist). After the client stops, the
status flips to `No recent heartbeat` once `STALE_AFTER_SECONDS` (default 90)
elapses.

### 8. Demonstrate authentication rejection

```bash
# Read API without credentials -> 401
curl -s -i http://127.0.0.1:5000/api/computers | head -1
curl -s http://127.0.0.1:5000/api/computers

# Ingestion without credentials -> 401
curl -s -X POST http://127.0.0.1:5000/api/events \
  -H 'Content-Type: application/json' -d '{}'

# Ingestion token cannot read the dashboard
curl -s -o /dev/null -w '%{http_code}\n' \
  -H "Authorization: Bearer $AUTH_TOKEN" http://127.0.0.1:5000/api/computers

# Dashboard token cannot submit events
curl -s -o /dev/null -w '%{http_code}\n' -X POST \
  -H "Authorization: Bearer $DASHBOARD_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"event_id":"x","computer_id":"LAB-DEMO-01","event_type":"connected","occurred_at":"2026-10-11T14:07:00Z"}' \
  http://127.0.0.1:5000/api/events
```

Expected: `401` for every call above. The read API returns
`{"error":"authentication required"}`.

### 9. Optional: controlled network outage and recovery

This shows the persistent outbox retaining events while the server is down and
delivering them (without duplicates) after it returns.

```bash
# Stop the server: press Ctrl+C in the first terminal.
# While the server is down, run the synthetic client:
python3 -m client.agent --synthetic
# Expected: Heartbeat: transient / "still pending: 1" and exit code 1.
# Exit code 1 here means "not delivered yet", not a crash.
echo "exit code: $?"
```

Now restart the server (`python3 run.py` in the first terminal) and run the
client again:

```bash
python3 -m client.agent --synthetic
```

Expected: the previously queued event is delivered along with the new one;
`still pending: 0` and exit code 0. In the dashboard, every queued event
appears exactly once (the client keeps a stable `event_id`, and the server
de-duplicates on it).

### 10. Stop the client and server safely

- The client was never left running in this synthetic demo (each command
  exits on its own).
- To stop the real client, press **Ctrl+C** in its terminal; the outbox is
  committed after every change, so nothing is lost.
- Stop the server with **Ctrl+C** in the first terminal.

If running under systemd, use the service commands instead:

```bash
sudo systemctl stop exam-monitor-client
sudo systemctl stop exam-monitor-server
```

---

## 4. Physical USB acceptance checklist

These tests require a **real Ubuntu lab PC** with USB ports and a phone/tablet
camera (or a second person) to record evidence. They cannot be performed on
macOS or without hardware. Record the result and any evidence (screenshot,
client log line, event timestamp) for each row.

Preconditions: the server is running and reachable; the client is running on
the Ubuntu PC (`python3 -m client.agent`, or `systemctl start exam-monitor-client`);
`COMPUTER_ID` is set to the PC's real lab name; the dashboard is open and
signed in.

| # | Action | Expected result | Pass/Fail |
|---|--------|-----------------|-----------|
| P1 | Insert a USB **storage** device (e.g. a flash drive). | Within a few seconds a `connected` event appears in Event History for the correct computer ID, category `usb_storage`. | |
| P2 | Remove the same USB storage device. | A `disconnected` event appears for the same computer ID. | |
| P3 | Insert a USB **peripheral** (mouse/keyboard/headset). | A `connected` event appears with category `usb_peripheral` (or `unknown` if udev metadata is incomplete — see limitations). | |
| P4 | Remove the peripheral. | A `disconnected` event appears. | |
| P5 | Compare the dashboard `Computer ID` with the machine's configured `COMPUTER_ID`. | They match exactly; events are never attributed to the wrong PC. | |
| P6 | Show the device name/vendor/product in the event. | Values are populated from udev metadata (or `unknown`/blank), and are recognisable as the device you inserted. | |
| P7 | Confirm no file contents are read/copied. | The client log shows only metadata lines; no file paths from the device appear. Optionally, while the agent runs: `sudo strace -f -e trace=openat -p "$(pgrep -f client.agent)" 2>&1 \| grep -i '/media\|/mnt'` shows no access to the mounted device path, and no files are copied anywhere. | |
| P8 | Disconnect the server's network (unplug LAN cable), then insert/remove a USB device several times. | The client logs delivery failures but the events remain queued locally (outbox persists). | |
| P9 | Restore the network. | Queued events are delivered automatically on the next retry; each appears exactly once in the dashboard. | |
| P10 | Restart the client service while events are pending: `sudo systemctl restart exam-monitor-client`. | After restart the client reconnects, heartbeats resume, and any undelivered events are still in the outbox and get delivered. | |
| P11 | Restart the server: `sudo systemctl restart exam-monitor-server`. | The server comes back with the same database; previously recorded events and computers are still present. | |
| P12 | Run two or more lab PCs concurrently, each inserting/removing a device. | Events from each PC appear under the correct computer ID; counts and heartbeats are per-PC. | |
| P13 | With all clients running, leave the room quiet for ~2 minutes. | Every PC shows `Heartbeat OK` even with no USB activity. | |
| P14 | Stop the client service and wait longer than `STALE_AFTER_SECONDS`. | That PC flips to `No recent heartbeat`; other PCs remain `Heartbeat OK`. | |
| P15 | A PC that has never sent a heartbeat (e.g. has an event but the agent never started). | Shows `No heartbeat yet`. | |
| P16 | Open `http://<server-ip>:5000/` in a browser without signing in. | Redirected to the login page; the dashboard is not readable. | |
| P17 | `curl http://<server-ip>:5000/api/computers` with no credentials. | `401 {"error":"authentication required"}`. | |
| P18 | `curl -X POST http://<server-ip>:5000/api/events` with no token. | `401`; nothing is stored. | |
| P19 | Sign in with the wrong dashboard password. | `401` with a generic "Invalid credentials." message; no secret is shown. | |
| P20 | Confirm the client token cannot read the dashboard and the dashboard token cannot post events. | Both return `401`. | |

### Notes for the checklist

- **`usb_peripheral` vs `unknown`:** classification is heuristic and based only
  on udev metadata. Some devices (especially composite or cheap devices) report
  incomplete data and are labelled `unknown`. This is expected behaviour, not a
  failure, provided the event itself is recorded.
- **Permissions:** if no events appear, the agent may lack udev access. Ensure
  the service account is in the `plugdev` group (or run as root) and check
  `journalctl -u exam-monitor-client -n 50`.
- **Do not** interpret a green heartbeat or any event as proof of cheating or
  of a secure machine.
