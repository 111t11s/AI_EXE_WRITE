#!/usr/bin/env bash
set -euo pipefail

# Print only the interpreter path to stdout so callers can capture it.
# Keep managed Python outside /root: systemd ProtectHome blocks that directory.
resolve_python() {
  local project_dir="$1"
  local python_bin="${PYTHON_BIN:-}"
  if [[ -z "$python_bin" ]]; then
    if [[ -x "$project_dir/.venv/bin/python" ]]; then
      python_bin="$project_dir/.venv/bin/python"
    elif command -v python3.12 >/dev/null 2>&1; then
      python_bin="$(command -v python3.12)"
    else
      local uv_bin="$project_dir/.build-tools/uv/uv"
      if [[ ! -x "$uv_bin" ]]; then
        if ! command -v curl >/dev/null 2>&1; then
          echo "Install curl and ca-certificates first (Ubuntu: sudo apt install -y curl ca-certificates)." >&2
          return 1
        fi
        local installer
        installer="$(mktemp)" || return 1
        echo "Python 3.12 is unavailable; installing uv for this project." >&2
        if ! curl --fail --show-error --location --retry 3 \
          https://astral.sh/uv/install.sh -o "$installer"; then
          rm -f -- "$installer"
          return 1
        fi
        if ! UV_INSTALL_DIR="$project_dir/.build-tools/uv" UV_NO_MODIFY_PATH=1 \
          sh "$installer" >&2; then
          rm -f -- "$installer"
          return 1
        fi
        rm -f -- "$installer"
      fi
      # These downloads need outbound access to Astral/GitHub.
      if ! UV_PYTHON_INSTALL_DIR="$project_dir/.build-tools/python" \
        "$uv_bin" python install 3.12 >&2; then
        return 1
      fi
      python_bin="$(UV_PYTHON_INSTALL_DIR="$project_dir/.build-tools/python" \
        "$uv_bin" python find --managed-python 3.12)" || return 1
    fi
  fi
  if ! "$python_bin" -c 'import sys; assert (3, 10) <= sys.version_info[:2] < (3, 14), "Python 3.10-3.13 required; 3.12 recommended"'; then
    echo "Unsupported or unavailable Python: $python_bin. Set PYTHON_BIN to a Python 3.10-3.13 executable." >&2
    return 1
  fi
  printf '%s\n' "$python_bin"
}

main() {
  if [[ "$EUID" -ne 0 ]]; then
    echo "Run with sudo: sudo bash deployment/install.sh" >&2
    exit 1
  fi
  cd "$(dirname "$0")/.."
  if [[ "$(pwd -P)" != "/opt/aitxt" ]]; then
    echo "Place the project in /opt/aitxt before installing." >&2
    exit 1
  fi
  local python_bin
  python_bin="$(resolve_python "$(pwd -P)")"
  echo "Using Python: $python_bin"
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
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
