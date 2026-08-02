"""Uvicorn entrypoint: run with `python -m server`.

Listens on all interfaces so every device on the LAN can reach the control
plane. Override with HOLDFAST_HOST / HOLDFAST_PORT if needed.
"""

import os

import uvicorn


def main() -> None:
    host = os.environ.get("HOLDFAST_HOST", "0.0.0.0")
    port = int(os.environ.get("HOLDFAST_PORT", "8000"))
    uvicorn.run("server:app", host=host, port=port)


if __name__ == "__main__":
    main()
