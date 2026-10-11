#!/usr/bin/env bash
#
# Automated installer for the USB monitoring CENTRAL SERVER on Ubuntu.
#
# Run this on the PC that will act as the server:
#
#   sudo ./deploy/install-server.sh
#
# It is idempotent: running it again updates the code and restarts the service
# without overwriting existing secrets. See LAB_SETUP.md for the manual steps.

set -euo pipefail

SERVICE_NAME="exam-monitor-server"
SERVICE_USER="exam-monitor"
DEST="/opt/exam-monitor"
CONF_DIR="/etc/exam-monitor"
STATE_DIR="/var/lib/exam-monitor"
ENV_FILE="$CONF_DIR/server.env"
PORT="${PORT:-5000}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }

gen_secret() {
    if command -v python3 >/dev/null 2>&1; then
        python3 -c 'import secrets; print(secrets.token_hex(32))'
    elif command -v openssl >/dev/null 2>&1; then
        openssl rand -hex 32
    else
        die "Need python3 or openssl to generate secrets."
    fi
}

[ "$(id -u)" -eq 0 ] || die "Please run with sudo: sudo $0"
[ -f "$SRC_ROOT/requirements.txt" ] || die "requirements.txt not found in $SRC_ROOT; run this from the project's deploy/ folder."

log "Installing system packages"
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip rsync curl >/dev/null

log "Creating service account and directories"
id -u "$SERVICE_USER" >/dev/null 2>&1 || useradd --system --home "$DEST" --shell /usr/sbin/nologin "$SERVICE_USER"
install -d -o root -g root -m 0755 "$DEST"
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0750 "$STATE_DIR"
install -d -o root -g root -m 0750 "$CONF_DIR"

log "Copying project to $DEST"
if [ "$SRC_ROOT" != "$DEST" ]; then
    rsync -a --delete \
        --exclude 'venv' --exclude '.git' --exclude '__pycache__' \
        --exclude '*.pyc' --exclude '*.db' --exclude '.pytest_cache' \
        "$SRC_ROOT/" "$DEST/"
fi

log "Creating virtual environment and installing dependencies"
if [ ! -x "$DEST/venv/bin/gunicorn" ]; then
    python3 -m venv "$DEST/venv"
fi
"$DEST/venv/bin/pip" install --upgrade pip >/dev/null
"$DEST/venv/bin/pip" install -r "$DEST/requirements.txt" >/dev/null

if [ ! -f "$ENV_FILE" ]; then
    log "Generating secrets in $ENV_FILE"
    AUTH_TOKEN="$(gen_secret)"
    DASHBOARD_TOKEN="$(gen_secret)"
    SECRET_KEY="$(gen_secret)"
    cat > "$ENV_FILE" <<EOF
APP_ENV=production
DATABASE_PATH=$STATE_DIR/exam_monitor.db
HOST=0.0.0.0
PORT=$PORT
AUTH_TOKEN=$AUTH_TOKEN
DASHBOARD_TOKEN=$DASHBOARD_TOKEN
SECRET_KEY=$SECRET_KEY
STALE_AFTER_SECONDS=90
SQLITE_BUSY_TIMEOUT_MS=5000
SESSION_LIFETIME_HOURS=12
SESSION_COOKIE_SECURE=false
EOF
    chown root:"$SERVICE_USER" "$ENV_FILE"
    chmod 0640 "$ENV_FILE"
    GENERATED=1
else
    warn "$ENV_FILE already exists; keeping existing secrets."
    GENERATED=0
fi

log "Installing systemd service"
install -m 0644 "$DEST/deploy/$SERVICE_NAME.service" "/etc/systemd/system/$SERVICE_NAME.service"
systemctl daemon-reload
systemctl enable --now "$SERVICE_NAME"
sleep 2

log "Service status"
systemctl --no-pager --full status "$SERVICE_NAME" || true

# shellcheck disable=SC1090
set -a; . "$ENV_FILE"; set +a

echo
log "Server is running. Open the dashboard from any lab PC at:"
echo "    http://<this-machine-lan-ip>:$PORT/"
echo
echo "Dashboard login password (DASHBOARD_TOKEN): $DASHBOARD_TOKEN"
echo "Client ingestion token      (AUTH_TOKEN):   $AUTH_TOKEN"
echo
if [ "$GENERATED" -eq 0 ]; then
    echo "These values are read back from the existing $ENV_FILE."
fi
echo "Keep the AUTH_TOKEN to pass to each client, e.g.:"
echo "    sudo ./deploy/install-client.sh --computer-id LAB-PC-01 --server-url http://<server-ip>:$PORT --token '$AUTH_TOKEN'"
echo
log "Health check:"
curl -fsS "http://127.0.0.1:$PORT/api/health" && echo || warn "Health check failed; see: journalctl -u $SERVICE_NAME -n 50 --no-pager"
echo
warn "If a firewall is active, allow the lab subnet only, e.g.:"
echo "    sudo ufw allow from 192.168.1.0/24 to any port $PORT proto tcp"
