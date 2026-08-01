"""
Minimal test for Phase 2 server components
"""

import os
import tempfile

from fastapi.testclient import TestClient

from server import create_app, init_database


def test_server_structure():
    """Test that server structure is in place."""
    with tempfile.TemporaryDirectory() as temp_dir:
        app = create_app(os.path.join(temp_dir, "test.db"))
        paths = {path for route in app.routes if (path := getattr(route, "path", None))}
        assert "/healthz" in paths
        assert "/api/v1/enroll" in paths


def test_database_creation():
    """Test that database can be initialized."""
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = os.path.join(temp_dir, "test.db")
        
        import sqlite3
        init_database(db_path)
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tokens'")
        result = cursor.fetchone()
        conn.close()
        
        assert result is not None


def test_imports():
    """Test that key modules can be imported."""
    with tempfile.TemporaryDirectory() as temp_dir:
        client = TestClient(create_app(os.path.join(temp_dir, "test.db")))
        assert client.get("/healthz").json() == {"status": "healthy"}


if __name__ == "__main__":
    test_server_structure()
    test_database_creation()
    test_imports()
    print("All server tests passed!")
