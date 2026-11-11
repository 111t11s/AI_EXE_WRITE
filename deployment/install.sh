#!/usr/bin/env bash
set -euo pipefail

if [[ "$EUID" -ne 0 ]]; then
  echo "Run with sudo: sudo bash deployment/install.sh" >&2
  exit 1
fi
cd "$(dirname "$0")/.."
if [[ "$(pwd -P)" != "/opt/aitxt" ]]; then
  echo "Extract the server package into /opt/aitxt before installing." >&2
  exit 1
fi
python_bin=python3.12
"$python_bin" -c 'import sys; assert (3, 10) <= sys.version_info[:2] < (3, 14), "Python 3.10-3.13 required; 3.12 recommended"'
if ! id aitxt >/dev/null 2>&1; then
  useradd --system --home-dir /var/lib/aitxt --create-home --shell /usr/sbin/nologin aitxt
fi
install -d -o aitxt -g aitxt -m 0750 /var/log/aitxt /var/lib/aitxt
if [[ ! -f .env ]]; then
  install -o root -g aitxt -m 0640 deployment/.env.example .env
  "$python_bin" - <<'PY'
from pathlib import Path
import secrets
p = Path(".env")
p.write_text(p.read_text().replace("APP_ACCESS_KEY=\n", "APP_ACCESS_KEY=" + secrets.token_urlsafe(24) + "\n"))
PY
fi
if [[ ! -x .venv/bin/python ]]; then
  "$python_bin" -m venv .venv
fi
.venv/bin/python -m pip install -r requirements-server.txt
chown root:aitxt .env
chmod 0640 .env
chmod o+x /opt/aitxt
install -o root -g root -m 0644 deployment/aitxt.service /etc/systemd/system/aitxt.service
systemctl daemon-reload
echo "Installed. Edit /opt/aitxt/.env, initialize MySQL if needed, then:"
echo "  sudo systemctl enable --now aitxt"
echo "  curl --fail http://127.0.0.1:8000/healthz"
