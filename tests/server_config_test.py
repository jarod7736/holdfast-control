"""
Tests for control-plane database-path resolution in create_app.
"""

from pathlib import Path

from server import create_app


def test_env_var_sets_database_path(tmp_path, monkeypatch):
    """HOLDFAST_DB_PATH is used when no explicit path is passed, and parents are created."""
    db = tmp_path / "data" / "control-plane.db"
    monkeypatch.setenv("HOLDFAST_DB_PATH", str(db))
    app = create_app()
    assert app.state.database_path == str(db)
    assert db.exists()


def test_explicit_argument_wins_over_env_var(tmp_path, monkeypatch):
    """An explicit database_path argument takes precedence over the environment."""
    monkeypatch.setenv("HOLDFAST_DB_PATH", str(tmp_path / "from-env.db"))
    explicit = tmp_path / "explicit.db"
    app = create_app(str(explicit))
    assert app.state.database_path == str(explicit)


def test_default_path_when_env_var_unset(tmp_path, monkeypatch):
    """Without the env var, the path falls back to ~/.holdfast/control-plane.db."""
    monkeypatch.delenv("HOLDFAST_DB_PATH", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    app = create_app()
    assert app.state.database_path == str(Path(tmp_path) / ".holdfast" / "control-plane.db")


def test_empty_env_var_falls_back_to_default(tmp_path, monkeypatch):
    """An empty HOLDFAST_DB_PATH is treated as unset, not as a path of ''."""
    monkeypatch.setenv("HOLDFAST_DB_PATH", "")
    monkeypatch.setenv("HOME", str(tmp_path))
    app = create_app()
    assert app.state.database_path == str(Path(tmp_path) / ".holdfast" / "control-plane.db")
