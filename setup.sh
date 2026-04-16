#!/usr/bin/env bash
# setup.sh — install and configure the ShopGoodwill watcher on Oracle Linux 8 / RHEL 8
# Run as your normal user (not root). Will sudo when needed.
#
# Usage:
#   chmod +x setup.sh
#   ./setup.sh

set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$APP_DIR/venv"
SERVICE_NAME="sgw-watches"
CRON_HOUR="6"   # Run scraper daily at 6 AM (server local time). Change to taste.

echo "==> App directory: $APP_DIR"

# ── 1. System packages ────────────────────────────────────────────────────────
echo "==> Installing system packages…"

# Enable the Oracle Linux / RHEL 8 Python 3.9 module if not already available
sudo dnf install -y python39 python39-pip

# ── 2. Python virtualenv ──────────────────────────────────────────────────────
echo "==> Creating virtualenv at $VENV…"
python3.9 -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet requests flask gunicorn

# ── 3. Run an initial scrape so the DB exists before the web app starts ───────
echo "==> Running initial scrape…"
"$VENV/bin/python" "$APP_DIR/scraper.py"

# ── 4. systemd service for the Flask app ─────────────────────────────────────
echo "==> Installing systemd service: $SERVICE_NAME…"

sudo tee /etc/systemd/system/${SERVICE_NAME}.service > /dev/null << EOF
[Unit]
Description=ShopGoodwill Men's Watches web UI
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$APP_DIR
ExecStart=$VENV/bin/gunicorn --bind 0.0.0.0:5000 --workers 2 app:app
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now ${SERVICE_NAME}.service
echo "    Service status:"
sudo systemctl status ${SERVICE_NAME}.service --no-pager -l | head -20

# ── 5. Open port 5000 in firewalld ────────────────────────────────────────────
# Oracle Linux 8 uses firewalld by default (unlike Ubuntu which uses ufw).
echo "==> Opening port 5000 in firewalld…"
if systemctl is-active --quiet firewalld; then
    sudo firewall-cmd --permanent --add-port=5000/tcp
    sudo firewall-cmd --reload
    echo "    Port 5000 opened."
else
    echo "    firewalld is not running — skipping. Open port 5000 manually if needed."
fi

# ── 6. SELinux: allow gunicorn to bind to port 5000 ──────────────────────────
# Oracle Linux 8 ships with SELinux enforcing by default, which will block
# gunicorn from binding to non-standard ports unless we tell it to allow it.
echo "==> Configuring SELinux for port 5000…"
if command -v semanage &>/dev/null; then
    sudo semanage port -a -t http_port_t -p tcp 5000 2>/dev/null \
        || sudo semanage port -m -t http_port_t -p tcp 5000
    echo "    SELinux: port 5000 labelled http_port_t."
else
    echo "    semanage not found — installing policycoreutils-python-utils…"
    sudo dnf install -y policycoreutils-python-utils
    sudo semanage port -a -t http_port_t -p tcp 5000 2>/dev/null \
        || sudo semanage port -m -t http_port_t -p tcp 5000
    echo "    SELinux: port 5000 labelled http_port_t."
fi

# ── 7. Daily cron job for the scraper ────────────────────────────────────────
echo "==> Installing daily cron job (${CRON_HOUR}:00)…"

CRON_CMD="0 ${CRON_HOUR} * * * $VENV/bin/python $APP_DIR/scraper.py >> $APP_DIR/scraper.log 2>&1"

# Add only if not already present
( crontab -l 2>/dev/null | grep -v "scraper.py" ; echo "$CRON_CMD" ) | crontab -

echo "    Current crontab:"
crontab -l

# ── 8. Done ───────────────────────────────────────────────────────────────────
echo ""
echo "✅  Setup complete!"
echo ""
echo "    Web UI:    http://<your-vm-ip>:5000"
echo "    Scraper:   runs daily at ${CRON_HOUR}:00, logs → $APP_DIR/scraper.log"
echo "    Database:  $APP_DIR/watches.db"
echo ""
echo "Useful commands:"
echo "    sudo systemctl status $SERVICE_NAME     # check web app"
echo "    sudo systemctl restart $SERVICE_NAME    # restart web app"
echo "    journalctl -u $SERVICE_NAME -f          # tail web app logs"
echo "    crontab -l                              # view cron schedule"
echo "    tail -f $APP_DIR/scraper.log            # tail scraper logs"
