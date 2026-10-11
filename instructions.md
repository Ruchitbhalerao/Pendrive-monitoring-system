# USB Device Monitoring and Alert System --- Run & Test Instructions

This guide explains how to run the project on your development Mac,
demonstrate it with a synthetic client, and prepare to test it on Ubuntu
computers in a college lab.

> **Important:** You can test the backend, dashboard, authentication,
> heartbeats, event delivery, and retry queue now on macOS using
> synthetic mode. Real USB monitoring through `udev`/`pyudev` and
> `systemd` service testing must be performed on Linux/Ubuntu. A
> successful synthetic test does not prove that physical USB detection
> works.

## 1. What you need

### For a local test on macOS

-   Python 3 installed.
-   The project folder (`Exam-monitor`).
-   A terminal.
-   A web browser.

### For testing in the college lab

-   One Ubuntu computer to act as the central server.
-   One or more Ubuntu lab computers to act as monitored clients.
-   Permission to install packages and configure services/firewall
    rules, or help from the lab administrator.
-   A working LAN connection between the server and clients.
-   A USB device for physical insertion/removal tests.

The central server and client PCs must be able to reach one another over
the college LAN. They do not need internet access at runtime once Python
dependencies have been installed.

## 2. Open the project

In Terminal, change to the directory containing the project. For
example:

``` bash
cd "/path/to/Exam-monitor"
```

Replace the example path with the actual path on your computer. Confirm
that you are in the project root:

``` bash
pwd
ls
```

You should see project files/folders such as `server`, `client`,
`tests`, `run.py`, `wsgi.py`, `requirements.txt`, and `README.md`.

Do not run the commands below from inside `server/` or `client/`; run
them from the project root unless a step explicitly says otherwise.

## 3. Create and activate a virtual environment

### macOS

``` bash
python3 -m venv venv
source venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### Ubuntu

``` bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip
python3 -m venv venv
source venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

If the project is deployed as systemd services, follow the deployment
section of the project's `README.md` as well. A service should use the
intended virtual environment and service account rather than relying on
an interactive shell.

## 4. Run the automated tests first

From the project root, with the virtual environment activated:

``` bash
python3 -m pytest -q
```

The last reported result was **133 passed**, but run the command again
on your current checkout and trust the result it prints.

Check Python syntax:

``` bash
python3 -m compileall client server tests run.py wsgi.py
```

Check JavaScript syntax if Node.js is installed:

``` bash
node --check server/static/js/common.js
node --check server/static/js/dashboard.js
node --check server/static/js/events.js
```

If `pytest` or a dependency is missing, make sure the virtual
environment is active and install the project's requirements. Record any
failures instead of assuming the test passed.

## 5. Configure local development secrets

The app uses separate credentials for event ingestion and dashboard/read
access. Do not reuse the ingestion token as the dashboard credential.

In the terminal where you will run the server, set temporary development
values:

``` bash
export APP_ENV=development
export HOST=127.0.0.1
export PORT=5000
export AUTH_TOKEN="local-ingestion-token-change-me"
export DASHBOARD_TOKEN="local-dashboard-token-change-me"
export SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
```

These example tokens are only for a local throwaway test. Generate
strong unique secrets for any shared machine or deployment. Do not
commit real `.env` files or put secrets in screenshots, source code, or
service unit files.

Check `.env.example` and `README.md` for all required variables and
their exact meanings. If the application rejects a configuration, fix
the configuration rather than disabling the validation.

## 6. Initialize the database and start the server

In **Terminal 1**, from the project root with the virtual environment
activated and the environment variables set:

``` bash
python run.py init-db
python run.py
```

Keep this terminal open while testing. The development server should
print startup information. If startup fails, read the error and check
the environment variables, dependencies, and current directory.

For local testing, `HOST=127.0.0.1` keeps the server accessible only
from the same computer. Do not bind to `0.0.0.0` on a public or
untrusted network.

Open the health endpoint in a browser:

``` text
http://127.0.0.1:5000/api/health
```

It should return a healthy JSON response. The health endpoint is
intended to be available without dashboard login.

## 7. Test the dashboard

Open:

``` text
http://127.0.0.1:5000/
```

You should be redirected to the login page if you are not authenticated.
Use the dashboard credential configured for this application (check the
current README and login implementation if the credential name differs).

After logging in, inspect the dashboard and event history. Depending on
whether the database contains events, you may see empty states
initially; that is expected.

Also open:

``` text
http://127.0.0.1:5000/events
```

Check that logout works and that protected pages no longer remain
accessible using the logged-out session. Do not assume the dashboard is
protected merely because the login page appears; the automated security
tests and the API checks below are also important.

## 8. Test a synthetic client on macOS

A Mac cannot run the project's Linux `udev` monitor. Use synthetic mode
to test the client/server communication without physical USB detection.

Open **Terminal 2** in the project root. Activate the same virtual
environment:

``` bash
source venv/bin/activate
```

Set the client environment. Use the same ingestion token as the server:

``` bash
export COMPUTER_ID="DEV-MAC-01"
export SERVER_URL="http://127.0.0.1:5000"
export AUTH_TOKEN="local-ingestion-token-change-me"
```

Run a synthetic connection event:

``` bash
python -m client.agent --synthetic --event-type connected
```

Use the exact command supported by the current project. The client
should report that it delivered a heartbeat and event, or show a clear
error if delivery failed.

Return to the dashboard and refresh the page. Confirm that `DEV-MAC-01`
appears and that the synthetic event is visible. **Label it as
simulated; it is not evidence of a physical USB connection.**

If the command exits after sending one event, that may be the intended
behavior of synthetic mode. For continuous heartbeat behavior, inspect
the documented synthetic-mode options and use the project's supported
command rather than guessing flags.

## 9. Verify API access rules

You can check that protected APIs reject unauthenticated requests from a
terminal. For example:

``` bash
curl -i http://127.0.0.1:5000/api/computers
curl -i http://127.0.0.1:5000/api/events
```

Without dashboard authentication, these should return an unauthorized
response. The health endpoint should remain accessible:

``` bash
curl -i http://127.0.0.1:5000/api/health
```

The dashboard read token is separate from the client ingestion token.
Use the exact documented bearer-token format for read API checks; do not
put a real token in a shared terminal recording or public report.

## 10. Test retry and recovery behavior

The project has a persistent client outbox. The intended recovery flow
is:

1.  Start the server.
2.  Run the synthetic client and confirm delivery.
3.  Stop the server in Terminal 1 with `Ctrl+C`.
4.  Run the documented synthetic/outage test procedure from
    `docs/ACCEPTANCE.md` or the README.
5.  Confirm that a failed delivery is retained in the outbox.
6.  Start the server again.
7.  Run the documented client command to retry pending items.
8.  Confirm that the pending queue drains and that the server does not
    store duplicate copies of the same event ID.

The exact synthetic command lifecycle matters: if the synthetic command
exits after one run, use the project's documented outage demonstration
steps to restart it. Do not delete the outbox database to make a test
pass. Use a throwaway test computer ID/database if you need an isolated
demonstration.

## 11. Testing on college Ubuntu machines

For a lab test, designate one machine as the **server** and the other
machine(s) as **clients**.

### 11.1 Choose the server

On the Ubuntu server, install the project and dependencies using the
deployment instructions in `README.md`. Configure unique strong values
for:

-   `AUTH_TOKEN`
-   `DASHBOARD_TOKEN`
-   `SECRET_KEY`
-   `APP_ENV=production`
-   `HOST` and `PORT`
-   the database path and other required settings documented by the
    project

Use the WSGI/Gunicorn service and systemd unit files for a real
deployment, rather than leaving the Flask development server running.
Follow the actual service names and paths in the repository; do not
guess them.

### 11.2 Find the server's LAN address

On the server, run:

``` bash
hostname -I
```

Use the appropriate private LAN address provided by the college network
administrator. Avoid posting the address publicly. If multiple addresses
are listed, identify the interface that clients use to communicate with
the server.

### 11.3 Configure server network access

The server must listen on the interface reachable by clients. Configure
`HOST` and the WSGI/service environment according to the README and
service files. Ensure the college firewall permits the configured
application port from the lab client network.

Do not expose the application port to the public internet. Follow
college IT policy before changing firewall settings. The application
uses plain HTTP unless TLS has separately been configured, so
credentials can be observed by someone able to capture that traffic. Use
HTTPS where possible or get explicit approval for the isolated lab
network and its risks.

### 11.4 Install each client

On every monitored Ubuntu PC:

1.  Install Python and the project dependencies.
2.  Configure the same ingestion `AUTH_TOKEN` as the server.
3.  Set `SERVER_URL` to the server's reachable LAN URL, for example
    `http://192.168.1.20:5000` (replace this example address and port).
4.  Assign a unique ID, such as `LAB-PC-01`, `LAB-PC-02`, and
    `LAB-PC-03`.
5.  Configure the heartbeat interval as documented, defaulting to 30
    seconds.
6.  Use the documented systemd client service for persistent monitoring
    and verify its permissions for USB monitoring.
7.  Do not run the client as root unless the deployment documentation
    and administrator explicitly require it.

Do not copy the dashboard credential onto the clients. Clients need the
ingestion token, not the professor's dashboard login credential.

### 11.5 Start and inspect the services

Use the actual unit names listed in the repository's README and systemd
files. Typical systemd operations are:

``` bash
sudo systemctl status <actual-server-service-name>
sudo systemctl status <actual-client-service-name>
sudo journalctl -u <actual-service-name> -n 100 --no-pager
```

Replace the placeholders with the real service names. To enable, start,
stop, or restart services, follow the documented deployment procedure.
Do not paste secrets into journal messages.

On the server, confirm that the health endpoint responds locally. From a
client, request the server's health URL using the server LAN IP and
configured port. If it fails, check the server service, listening
address, firewall, client URL, and LAN connectivity.

## 12. Physical USB test on Ubuntu

Only perform these steps on a machine you are authorized to test.

1.  Confirm the client service is running.
2.  Log into the professor dashboard on an authorized device.
3.  Note the client computer ID and current heartbeat status.
4.  Insert a USB storage device.
5.  Check whether a connection event appears with the expected computer
    ID and classification.
6.  Safely remove the device and check whether a disconnection event
    appears.
7.  Repeat with a non-storage USB peripheral if available.
8.  Check timestamps and verify events are associated with the correct
    client.
9.  Disconnect the server network temporarily, generate an event,
    restore the network, and verify the pending event is delivered.
10. Restart the client service and server separately and verify that
    database history and retry behavior remain correct.

The monitor is designed to collect device metadata, not read or copy
files from the USB device. Do not use personal or sensitive USB contents
as part of the test.

## 13. Multi-computer test

For two or more clients:

-   Give every client a different `COMPUTER_ID`.
-   Ensure all clients use the correct server URL and ingestion token.
-   Confirm each client has its own persistent outbox.
-   Generate a synthetic event on each client first.
-   Verify the dashboard shows separate computers and associates events
    correctly.
-   Disconnect one client while another remains connected; the second
    should continue reporting.
-   Check heartbeat status after the configured stale threshold.

A recent heartbeat means the client reported recently. It does not prove
the client is secure, that every USB event was captured, or that
cheating did or did not occur.

## 14. Troubleshooting

### Server will not start

-   Activate the correct virtual environment.
-   Confirm dependencies are installed.
-   Check required environment variables and production secret
    validation.
-   Check whether the configured port is already in use.
-   Read the server/service logs without sharing secrets.

### Client reports authentication failure

-   Verify the client `AUTH_TOKEN` exactly matches the server's
    ingestion token.
-   Do not use `DASHBOARD_TOKEN` as the client token.
-   Check the client server URL and whether it includes the correct
    scheme, host, and port.

### Client cannot reach the server

-   Check the server service and health endpoint.
-   Verify that `SERVER_URL` uses the server's LAN address, not
    `127.0.0.1` on a different computer.
-   Check firewall rules and college network restrictions.
-   Do not disable authentication to work around a connectivity problem.

### Computer appears stale

-   Check the client service status and logs.
-   Verify the client can reach the server and its ingestion token is
    valid.
-   Check the heartbeat interval and stale threshold.
-   Remember that a stale status indicates a missing recent heartbeat,
    not necessarily a hardware fault.

### USB devices are not detected on Ubuntu

-   Confirm the client service is running on Linux.
-   Inspect the service logs.
-   Check whether `pyudev` is installed in the service's virtual
    environment.
-   Follow the project's documented udev/group permissions and ask the
    lab administrator for help.
-   Do not solve permission problems by granting broad, permanent root
    access without understanding the consequences.

### Events remain pending

-   Confirm the server is reachable and healthy.
-   Check the ingestion token and server URL.
-   Inspect client logs for transient errors versus authentication or
    validation errors.
-   Restore connectivity and use the documented retry workflow.

## 15. Acceptance checklist

Mark each item only after observing the result.

-   [ ] Automated test suite passes on the current checkout.
-   [ ] Server starts and the health endpoint responds.
-   [ ] Dashboard login works.
-   [ ] Unauthenticated dashboard and read API access is rejected.
-   [ ] Synthetic heartbeat is delivered.
-   [ ] Synthetic event appears in event history.
-   [ ] Invalid ingestion token is rejected.
-   [ ] Repeated event IDs do not create duplicates.
-   [ ] Outbox retains events during a controlled outage and drains
    after recovery.
-   [ ] Two clients appear as separate computers.
-   [ ] Ubuntu systemd services start and restart correctly.
-   [ ] Physical USB insertion/removal is verified on Ubuntu.
-   [ ] No USB file contents are read or copied.
-   [ ] Backup and restore procedure is tested.

## 16. Security and interpretation reminders

-   Keep ingestion, dashboard, and session-signing secrets separate.
-   Never commit real secrets or share them in screenshots, logs, or
    reports.
-   Plain HTTP does not encrypt tokens in transit.
-   Restrict dashboard access to authorized staff.
-   Back up the SQLite database using a safe, consistent procedure.
-   A heartbeat indicates recent client reporting, not integrity or
    security.
-   A USB event is device activity metadata, not proof of cheating.
-   Synthetic events are for software demonstrations only and must be
    labelled as simulated.

For project-specific service names, environment paths, and the supported
outage-demo commands, use the current `README.md` and
`docs/ACCEPTANCE.md` as the source of truth.
