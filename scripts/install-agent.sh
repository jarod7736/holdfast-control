#!/usr/bin/env bash
#
# Install the user-scoped Holdfast Control agent and its systemd user timer.
# Works on WSL and native Linux. No sudo is required.
#
# Usage: scripts/install-agent.sh [REPO_ROOT]
#   REPO_ROOT defaults to the repository root (parent of this script).
set -euo pipefail

REPO_ROOT="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
AGENT_BIN="${HOME}/.local/bin/holdfastctl"
CONFIG_DIR="${HOME}/.config/holdfastctl"
CONFIG_FILE="${CONFIG_DIR}/config.yaml"
SYSTEMD_DIR="${HOME}/.config/systemd/user"

# Detect WSL (informational only — install proceeds either way).
if grep -qi microsoft /proc/sys/kernel/osrelease 2>/dev/null || [ -n "${WSL_INTEROP:-}" ]; then
    echo "[wsl] WSL environment detected"
else
    echo "[wsl] Not running under WSL (native Linux assumed)"
fi

if [ ! -d "${REPO_ROOT}/src/holdfastctl" ]; then
    echo "error: repo root not found at ${REPO_ROOT}" >&2
    exit 1
fi

# 1. Install the package into the user site-packages (idempotent).
echo "[install] Installing holdfastctl from ${REPO_ROOT}"
pip3 install --user --break-system-packages -e "${REPO_ROOT}" >/dev/null

if [ ! -x "${AGENT_BIN}" ]; then
    echo "error: agent binary not found at ${AGENT_BIN}" >&2
    exit 1
fi

# 2. Write the agent config (never overwrite an existing config).
#    Device identity: prefer the real hostname (hosts/DNS), else ask the user.
DEVICE_ID="${HOSTNAME:-$(hostname 2>/dev/null)}"
if [ -z "${DEVICE_ID}" ]; then
    printf 'Could not detect a device name. Device id (e.g. %s): ' "$(hostname 2>/dev/null || echo laptop)"
    read -r DEVICE_ID
fi

mkdir -p "${CONFIG_DIR}"
if [ -f "${CONFIG_FILE}" ]; then
    echo "[config] ${CONFIG_FILE} exists — leaving unchanged"
else
    cat > "${CONFIG_FILE}" <<EOF
device_id: ${DEVICE_ID}
control_plane_url: http://127.0.0.1:8000
schedule: 15m
EOF
    echo "[config] wrote ${CONFIG_FILE} (device_id: ${DEVICE_ID})"
fi

# 3. Install the systemd user units and enable the timer.
mkdir -p "${SYSTEMD_DIR}"
cp "${REPO_ROOT}/templates/systemd/holdfastctl-agent.service" "${SYSTEMD_DIR}/"
cp "${REPO_ROOT}/templates/systemd/holdfastctl-agent.timer" "${SYSTEMD_DIR}/"
systemctl --user daemon-reload
systemctl --user enable --now holdfastctl-agent.timer >/dev/null

echo
echo "[ok] Holdfast Control agent installed"
echo "     agent:    ${AGENT_BIN}"
echo "     config:   ${CONFIG_FILE}"
echo "     timer:    holdfastctl-agent.timer"
systemctl --user list-timers holdfastctl-agent.timer --no-pager | tail -n +1
