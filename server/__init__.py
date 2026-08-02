"""Holdfast control plane (FastAPI)."""

import os
from pathlib import Path

from fastapi import FastAPI

from server.api import create_router
from server.storage import init_database

__all__ = ["app", "create_app", "init_database"]


def create_app(database_path: str | None = None, admin_token: str | None = None) -> FastAPI:
    if database_path is None:
        database_path = str(Path.home() / ".holdfast" / "control-plane.db")
    if admin_token is None:
        admin_token = os.environ.get("HOLDFAST_ADMIN_TOKEN")
    init_database(database_path)

    app = FastAPI(title="Holdfast Control Plane", version="0.1.0")
    app.state.database_path = database_path
    app.state.admin_token = admin_token
    app.router.routes.extend(create_router(database_path, admin_token).routes)
    return app


app = create_app()
