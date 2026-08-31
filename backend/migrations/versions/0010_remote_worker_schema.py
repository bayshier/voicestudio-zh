"""Remote worker control-plane schema.

Revision ID: 0010_remote_worker_schema
Revises: 0009_generation_history_starred

Mirrors ``core.db::_BASE_SCHEMA`` while that startup schema remains the
fallback for bundled installs where Alembic is unavailable.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0010_remote_worker_schema"
down_revision: Union[str, None] = "0009_generation_history_starred"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(name: str) -> bool:
    row = op.get_bind().execute(
        sa.text("SELECT name FROM sqlite_master WHERE type='table' AND name=:name"),
        {"name": name},
    ).fetchone()
    return row is not None


def _has_column(table: str, column: str) -> bool:
    rows = op.get_bind().execute(sa.text(f"PRAGMA table_info({table})")).fetchall()
    return any(row[1] == column for row in rows)


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS remote_workers (
            id TEXT PRIMARY KEY, name TEXT NOT NULL DEFAULT '', key_id TEXT NOT NULL,
            public_key BLOB NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
            revoked INTEGER NOT NULL DEFAULT 0, revoked_at REAL,
            priority INTEGER NOT NULL DEFAULT 50, endpoint TEXT NOT NULL DEFAULT '',
            host_json TEXT NOT NULL DEFAULT '{}', capabilities_json TEXT NOT NULL DEFAULT '[]',
            max_concurrent_tasks INTEGER NOT NULL DEFAULT 1,
            session_epoch INTEGER NOT NULL DEFAULT 0, consent_granted_at REAL,
            created_at REAL NOT NULL, last_seen_at REAL
        )
    """)
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_remote_workers_key ON remote_workers(key_id)"
    )
    op.execute("""
        CREATE TABLE IF NOT EXISTS remote_worker_enrollments (
            token_id TEXT PRIMARY KEY, secret_hash TEXT NOT NULL,
            endpoint TEXT NOT NULL DEFAULT '', cert_fingerprint TEXT NOT NULL DEFAULT '',
            label TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL,
            expires_at REAL NOT NULL, used_at REAL, used_by_worker TEXT
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS remote_tasks (
            id TEXT PRIMARY KEY, idempotency_key TEXT, operation TEXT NOT NULL,
            engine TEXT NOT NULL DEFAULT '', model_id TEXT NOT NULL DEFAULT '',
            params_json TEXT NOT NULL DEFAULT '{}', priority INTEGER NOT NULL DEFAULT 0,
            state TEXT NOT NULL DEFAULT 'queued', max_attempts INTEGER NOT NULL DEFAULT 3,
            excluded_json TEXT NOT NULL DEFAULT '[]', error_json TEXT, result_ref TEXT,
            result_json TEXT, project_id TEXT, created_at REAL NOT NULL,
            updated_at REAL NOT NULL, deadline_at REAL, pinned_worker_id TEXT, finished_at REAL
        )
    """)
    if not _has_column("remote_tasks", "pinned_worker_id"):
        op.add_column("remote_tasks", sa.Column("pinned_worker_id", sa.Text(), nullable=True))
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_remote_tasks_state "
        "ON remote_tasks(state, priority, created_at)"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_remote_tasks_idem "
        "ON remote_tasks(idempotency_key) WHERE idempotency_key IS NOT NULL"
    )
    op.execute("""
        CREATE TABLE IF NOT EXISTS remote_task_attempts (
            id TEXT PRIMARY KEY, task_id TEXT NOT NULL, worker_id TEXT NOT NULL,
            session_epoch INTEGER NOT NULL DEFAULT 0, attempt_number INTEGER NOT NULL DEFAULT 1,
            state TEXT NOT NULL DEFAULT 'assigned', progress REAL NOT NULL DEFAULT 0,
            stage TEXT NOT NULL DEFAULT '', error_json TEXT, created_at REAL NOT NULL,
            accepted_at REAL, started_at REAL, finished_at REAL,
            lease_expires_at REAL, grace_expires_at REAL
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_remote_attempts_task ON remote_task_attempts(task_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_remote_attempts_worker "
        "ON remote_task_attempts(worker_id, state)"
    )


def downgrade() -> None:
    for table in (
        "remote_task_attempts",
        "remote_tasks",
        "remote_worker_enrollments",
        "remote_workers",
    ):
        if _has_table(table):
            op.drop_table(table)
