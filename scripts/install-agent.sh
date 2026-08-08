#!/usr/bin/env bash
#
# Install the user-scoped Holdfast Control agent and its systemd user timer.
# Works on WSL and native Linux. No sudo is required.
#
# Usage: scripts/install-agent.sh [REPO_ROOT] [--control-plane URL] [--enrollment-code CODE]
#                                 [--python INTERPRETER]
#   REPO_ROOT defaults to the repository root (parent of this script).
set -euo pipefail

CONTROL_PLANE_URL="https://holdfast.tail1c66ec.ts.net"
ENROLLMENT_CODE=""
PYTHON_BIN=""
POSITIONAL_REPO=""
while [ $# -gt 0 ]; do
    case "$1" in
        --control-plane) CONTROL_PLANE_URL="$2"; shift 2 ;;
        --enrollment-code) ENROLLMENT_CODE="$2"; shift 2 ;;
        --python) PYTHON_BIN="$2"; shift 2 ;;
        *) POSITIONAL_REPO="$1"; shift ;;
    esac
done
REPO_ROOT="${POSITIONAL_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
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

# Pick an interpreter that can install the agent. Two ways to get this wrong:
#   - too old: bare `pip3` resolves to something older than the package's
#     requires-python (>= 3.12) — pyenv shims typically do.
#   - a virtualenv: holdfastctl-agent.service hardcodes
#     ExecStart=%h/.local/bin/holdfastctl, so this must be a `pip install --user`,
#     which pip refuses from inside a virtualenv ("User site-packages are not
#     visible in this virtualenv").
# Prints why an interpreter is unusable; empty output means it is fine.
python_reject_reason() {
    command -v "$1" >/dev/null 2>&1 || { echo "not on PATH"; return 0; }
    "$1" -c '
import sys
if sys.version_info < (3, 12):
    print("Python %d.%d, but holdfastctl needs >= 3.12" % sys.version_info[:2])
elif sys.prefix != sys.base_prefix:
    print("inside a virtualenv, which cannot do the required --user install")
' 2>/dev/null || echo "not a working interpreter"
}

if [ -n "${PYTHON_BIN}" ]; then
    REJECT_REASON="$(python_reject_reason "${PYTHON_BIN}")"
    if [ -n "${REJECT_REASON}" ]; then
        echo "error: --python ${PYTHON_BIN} is ${REJECT_REASON}" >&2
        exit 1
    fi
else
    for candidate in python3.14 python3.13 python3.12 python3; do
        if [ -z "$(python_reject_reason "${candidate}")" ]; then
            PYTHON_BIN="${candidate}"
            break
        fi
    done
    if [ -z "${PYTHON_BIN}" ]; then
        echo "error: found no Python >= 3.12 outside a virtualenv." >&2
        echo "       If a venv is active, 'deactivate' first — the agent installs with" >&2
        echo "       'pip install --user' because systemd runs %h/.local/bin/holdfastctl." >&2
        echo "       Pass one explicitly:  scripts/install-agent.sh --python /path/to/python3.12" >&2
        echo "       On a pyenv box:       PYENV_VERSION=3.12.0 scripts/install-agent.sh ..." >&2
        exit 1
    fi
fi

# Resolve to the real executable. pip writes the interpreter path into the
# console script's shebang, and systemd runs the timer with no PYENV_VERSION
# set — a pyenv shim there would fall back to the global (old) Python.
PYTHON_BIN="$("${PYTHON_BIN}" -c 'import sys; print(sys.executable)')"

# 1. Install the package into the user site-packages (idempotent).
echo "[install] Installing holdfastctl from ${REPO_ROOT} using ${PYTHON_BIN}"
"${PYTHON_BIN}" -m pip install --user --break-system-packages -e "${REPO_ROOT}" >/dev/null

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
control_plane_url: ${CONTROL_PLANE_URL}
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

# 4. First report: enroll immediately when a code was provided.
if [ -n "${ENROLLMENT_CODE}" ]; then
    echo "[enroll] Running first report with enrollment code"
    "${AGENT_BIN}" report --enrollment-code "${ENROLLMENT_CODE}" || \
        echo "[enroll] WARNING: first report failed — run manually: ${AGENT_BIN} report --enrollment-code <code>" >&2
fi

echo
echo "[ok] Holdfast Control agent installed"
echo "     agent:    ${AGENT_BIN}"
echo "     config:   ${CONFIG_FILE}"
echo "     timer:    holdfastctl-agent.timer"
systemctl --user list-timers holdfastctl-agent.timer --no-pager | tail -n +1
