import os
import sqlite3


def _upgrade(db_path: str) -> None:
    from alembic import command
    from alembic.config import Config

    root = os.path.dirname(os.path.dirname(__file__))
    config = Config(os.path.join(root, "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(config, "head")


def test_remote_schema_upgrades_from_previous_head(tmp_path, monkeypatch):
    db_path = tmp_path / "remote.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE alembic_version (version_num VARCHAR(64) NOT NULL)")
        conn.execute(
            "INSERT INTO alembic_version VALUES ('0009_generation_history_starred')"
        )
    monkeypatch.setenv("OMNIVOICE_DB_PATH", str(db_path))

    _upgrade(str(db_path))

    with sqlite3.connect(db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert {
            "remote_workers",
            "remote_worker_enrollments",
            "remote_tasks",
            "remote_task_attempts",
        } <= tables
        columns = {row[1] for row in conn.execute("PRAGMA table_info(remote_tasks)")}
        assert "pinned_worker_id" in columns
