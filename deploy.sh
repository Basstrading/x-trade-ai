#!/bin/bash
# ═══════════════════════════════════════════════
# x-trade.ai — Deploy on VPS
# Run this ON the VPS (or it runs automatically via SSH)
# ═══════════════════════════════════════════════

set -e

APP_DIR="/opt/x-trade"
REPO_URL="$1"

echo "══════════════════════════════════════"
echo "  x-trade.ai — Deploying"
echo "══════════════════════════════════════"

# 1. System deps
echo "  [1/6] System packages..."
apt-get update -qq
apt-get install -y -qq python3 python3-pip python3-venv git nginx certbot python3-certbot-nginx > /dev/null

# 2. Clone or pull
if [ -d "$APP_DIR" ]; then
    echo "  [2/6] Pulling latest..."
    cd "$APP_DIR"
    git pull origin main
else
    echo "  [2/6] Cloning..."
    git clone "$REPO_URL" "$APP_DIR"
    cd "$APP_DIR"
fi

# 3. Python env
echo "  [3/6] Python environment..."
python3 -m venv venv
source venv/bin/activate
pip install -q -r requirements.txt

# 4. Create .env if missing
if [ ! -f .env ]; then
    echo "  [4/6] Creating .env..."
    cp .env.example .env
    # Generate admin key
    ADMIN_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(16))")
    echo "RISK_DESK_ADMIN_KEY=$ADMIN_KEY" >> .env
    echo "  Admin key: $ADMIN_KEY"
else
    echo "  [4/6] .env exists, keeping it"
fi

# 5. Create data dirs
echo "  [5/6] Data directories..."
mkdir -p data/risk_desk data/licenses logs

# 6. Systemd service
echo "  [6/6] Systemd service..."
cat > /etc/systemd/system/xtrade.service << 'EOF'
[Unit]
Description=x-trade.ai Risk Desk
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/x-trade
ExecStart=/opt/x-trade/venv/bin/python main.py
Restart=always
RestartSec=5
Environment=TRADING_PORT=8001

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable xtrade
systemctl restart xtrade

echo ""
echo "══════════════════════════════════════"
echo "  x-trade.ai — ONLINE"
echo "  http://$(hostname -I | awk '{print $1}'):8001"
echo "══════════════════════════════════════"
