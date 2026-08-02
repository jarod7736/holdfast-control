#!/usr/bin/env bash
#
# Take a consistent snapshot of the control-plane SQLite database so it can be
# copied to the Synology. Uses SQLite's online backup API, so the running
# control plane does not need to be stopped.
#
# Usage: scripts/snapshot-control-plane-db.sh [SOURCE_DB] [DEST_FILE]
set -euo pipefail

SOURCE_DB="${1:-${HOME}/.holdfast/control-plane.db}"
DEST_FILE="${2:-control-plane.db-snapshot}"

if [ ! -f "${SOURCE_DB}" ]; then
    echo "Error: source database not found: ${SOURCE_DB}" >&2
    exit 1
fi

PYTHON_BIN="$(command -v python3)"
"${PYTHON_BIN}" - "${SOURCE_DB}" "${DEST_FILE}" <<'PYTHON'
import sqlite3
import sys

source_path, dest_path = sys.argv[1], sys.argv[2]
source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
dest = sqlite3.connect(dest_path)
with dest:
    source.backup(dest)
counts = {
    table: dest.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    for table in ("tokens", "enrollment_codes", "gateway_keys")
}
source.close()
dest.close()
print(f"snapshot written to {dest_path}")
print("  " + ", ".join(f"{table}={count}" for table, count in counts.items()))
PYTHON

chmod 600 "${DEST_FILE}"
echo "Copy ${DEST_FILE} to the Synology as <HOLDFAST_DATA_DIR>/control-plane.db"
echo "Then: chown it to UID 10001 (or chmod 666) so the container can write it."
