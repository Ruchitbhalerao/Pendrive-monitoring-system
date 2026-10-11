#!/usr/bin/env bash
#
# Automated installer for a USB monitoring CLIENT agent on an Ubuntu lab PC.
#
# Run this on every monitored computer:
#
#   sudo ./deploy/install-client.sh \
#       --computer-id LAB-PC-01 \
#       --server-url http://192.168.1.10:5000 \
#       --token '<server AUTH_TOKEN>'
#
# Any value you omit is prompted for. It is idempotent: re-running updates the
# code and restarts the service without overwriting the existing config.
# See LAB_SETUP.md for the manual steps.

set -euo pipefail

SERVICE_NAME="exam-monitor-client"
SERVICE_USER="exam-monitor"
DEST="/opt/exam-monitor"
CONF_DIR="/etc/exam-monitor"
STATE_DIR="/var/lib/exam-monitor"
ENV_FILE="$CONF_DIR/client.env"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }

COMPUTER_ID="${COMPUTER_ID:-}"
SERVER_URL="${SERVER_URL:-}"
AUTH_TOKEN="${AUTH_TOKEN:-}"

while [ $# -gt 0 ]; do
    case "$1" in
        --computer-id) COMPUTER_ID="$2"; shift 2 ;;
        --server-url)  SERVER_URL="$2";  shift 2 ;;
        --token)       AUTH_TOKEN="$2";  shift 2 ;;
        -h|--help)
            echo "Usage: sudo $0 [--computer-id ID] [--server-url URL] [--token TOKEN]"
            exit 0 ;;
        *) die "Unknown argument: $1" ;;
    esac
done

[ "$(id -u)" -eq 0 ] || die "Please run with sudo: sudo $0"
[ -f "$SRC_ROOT/requirements.txt" ] || die "requirements.txt not found in $SRC_ROOT; run this from the project's deploy/ folder."

if [ -z "$COMPUTER_ID" ]; then
    read -r -p "Unique COMPUTER_ID for this PC (e.g. LAB-PC-01): " COMPUTER_ID
fi
if [ -z "$SERVER_URL" ]; then
    read -r -p "Server base URL (e.g. http://192.168.1.10:5000): " SERVER_URL
fi
if [ -z "$AUTH_TOKEN" ]; then
    read -r -p "Server AUTH_TOKEN (ingestion token): " AUTH_TOKEN
fi
[ -n "$COMPUTER_ID" ] || die "COMPUTER_ID is required."
[ -n "$SERVER_URL" ]  || die "SERVER_URL is required."
[ -n "$AUTH_TOKEN" ]  || die "AUTH_TOKEN is required."

log "Installing system packages"
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip rsync libudev1 >/dev/null

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
if [ ! -x "$DEST/venv/bin/python" ]; then
    python3 -m venv "$DEST/venv"
fi
"$DEST/venv/bin/pip" install --upgrade pip >/dev/null
"$DEST/venv/bin/pip" install -r "$DEST/requirements.txt" >/dev/null

log "Writing client config to $ENV_FILE"
cat > "$ENV_FILE" <<EOF
COMPUTER_ID=$COMPUTER_ID
SERVER_URL=$SERVER_URL
AUTH_TOKEN=$AUTH_TOKEN
CLIENT_DB_PATH=$STATE_DIR/client_outbox.db
RETRY_INTERVAL=5
REQUEST_TIMEOUT=10
HEARTBEAT_INTERVAL_SECONDS=30
EOF
chown root:"$SERVICE_USER" "$ENV_FILE"
chmod 0640 "$ENV_FILE"

log "Granting udev access (plugdev group)"
usermod -aG plugdev "$SERVICE_USER" || warn "Could not add to plugdev; USB detection may need the unit to run as root."

log "Installing systemd service"
install -m 0644 "$DEST/deploy/$SERVICE_NAME.service" "/etc/systemd/system/$SERVICE_NAME.service"
systemctl daemon-reload
systemctl enable --now "$SERVICE_NAME"
sleep 2

log "Service status"
systemctl --no-pager --full status "$SERVICE_NAME" || true

echo
log "Client '$COMPUTER_ID' installed and started."
echo "It will report to $SERVER_URL and appear on the dashboard within ~30 seconds."
echo "Logs:    journalctl -u $SERVICE_NAME -f"
echo "Restart: sudo systemctl restart $SERVICE_NAME"
echo "Stop:    sudo systemctl stop $SERVICE_NAME"
