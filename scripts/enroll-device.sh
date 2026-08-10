#!/usr/bin/env bash
# Enroll a device with the Holdfast control plane in one shot.
#
# Operator side: mints a one-time enrollment code (needs the admin token).
# Device side:   optionally redeems it on THIS machine via install-agent.sh.
#
# Variable inputs (device id, gateway models, MCP servers, redeem y/n) are read
# from stdin. Everything else is defaulted; override defaults via env vars.
set -euo pipefail

# --- defaults (override via env) ---
CONTROL_PLANE="${CONTROL_PLANE:-https://holdfast.tail1c66ec.ts.net}"
OP_ADMIN_REF="${OP_ADMIN_REF:-op://holdfast-automation/holdfast-control admin token/credential}"
EXPIRES="${EXPIRES:-600}"
AGENT="${AGENT:-holdfastctl}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

command -v "$AGENT" >/dev/null || { echo "error: '$AGENT' not on PATH" >&2; exit 1; }

# --- inputs from stdin ---
default_device="${HOSTNAME:-$(hostname 2>/dev/null || echo device)}"
read -rp "Device id [${default_device}]: " DEVICE_ID
DEVICE_ID="${DEVICE_ID:-$default_device}"
read -rp "Gateway models (comma-separated, blank = no gateway scope): " MODELS
read -rp "MCP servers (comma-separated, blank = none): " MCPS

# --- admin token (from env if present, else 1Password) ---
if [ -z "${HOLDFAST_ADMIN_TOKEN:-}" ]; then
  command -v op >/dev/null || { echo "error: op (1Password CLI) not found and HOLDFAST_ADMIN_TOKEN not set" >&2; exit 1; }
  echo "Reading admin token from 1Password: ${OP_ADMIN_REF}"
  HOLDFAST_ADMIN_TOKEN="$(op read "$OP_ADMIN_REF")"
fi
[ -n "$HOLDFAST_ADMIN_TOKEN" ] || { echo "error: admin token is empty" >&2; exit 1; }
export HOLDFAST_ADMIN_TOKEN

# --- mint the code ---
set -- enroll-code "$DEVICE_ID" --control-plane "$CONTROL_PLANE" --expires "$EXPIRES"
[ -n "$MODELS" ] && set -- "$@" --models "$MODELS"
[ -n "$MCPS" ]   && set -- "$@" --mcp "$MCPS"

echo "Minting: $AGENT ${*}"
if ! OUT="$("$AGENT" "$@" 2>&1)"; then
  echo "error: enroll-code failed:" >&2
  echo "$OUT" >&2
  echo "hint: if you passed --models/--mcp, those ids must be valid on the gateway; retry with none to test." >&2
  exit 1
fi
CODE="$(printf '%s\n' "$OUT" | awk '/--enrollment-code/{print $NF; exit}')"
[ -n "$CODE" ] || { echo "error: could not parse a code from output:" >&2; echo "$OUT" >&2; exit 1; }
echo "Enrollment code for '${DEVICE_ID}': ${CODE}  (valid ${EXPIRES}s)"

# --- optional redeem on this machine ---
redeem_cmd_device="$REPO_ROOT/scripts/install-agent.sh --control-plane $CONTROL_PLANE --enrollment-code $CODE"
if [ "$DEVICE_ID" != "$default_device" ]; then
  echo "note: install-agent.sh enrolls as hostname '${default_device}', but this code is bound to '${DEVICE_ID}'."
  echo "      Redeem only on a machine whose id resolves to '${DEVICE_ID}' (config device_id or hostname)."
fi
read -rp "Redeem now on THIS machine via install-agent.sh? [y/N]: " ANS
case "${ANS:-N}" in
  [yY]*)
    # An existing report token makes the agent SKIP enrollment (and skip key
    # minting) — it reuses the stored token and ignores the code. Force a fresh
    # enrollment by moving it aside, otherwise no gateway key is minted.
    TOKEN_FILE="${HOME}/.config/holdfastctl/report.token"
    if [ -f "$TOKEN_FILE" ]; then
      echo "note: existing report token at $TOKEN_FILE — the agent will reuse it and SKIP enrollment"
      echo "      (no gateway key minted, code ignored) unless it is moved aside."
      read -rp "Move it aside and force fresh enrollment? [y/N]: " FORCE
      case "${FORCE:-N}" in
        [yY]*) mv "$TOKEN_FILE" "${TOKEN_FILE}.bak.$(date +%s)"; echo "moved token aside; enrollment will run" ;;
        *)     echo "keeping token: enrollment will be skipped and NO gateway key minted" ;;
      esac
    fi
    exec "$REPO_ROOT/scripts/install-agent.sh" --control-plane "$CONTROL_PLANE" --enrollment-code "$CODE"
    ;;
  *)
    echo "Skipped redeem. To enroll the device, run ON that device:"
    echo "  $redeem_cmd_device"
    echo "or, if the agent is already configured there:"
    echo "  $AGENT report --enrollment-code $CODE --control-plane $CONTROL_PLANE"
    ;;
esac
