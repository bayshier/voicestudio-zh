# Alembic migrations

Schema evolution lives here going forward. The legacy hand-rolled
`_migrate()` function in `backend/core/db.py` is kept for the next release
as a fallback, and can be retired once Alembic has run in production.

## Workflow

From the repo root:

```bash
# Create a new migration
uv run alembic revision -m "add glossary table"

# Apply pending migrations
uv run alembic upgrade head

# Show current schema version
uv run alembic current

# Downgrade one step
uv run alembic downgrade -1
```

## Conventions

- **SQLite-safe:** `env.py` sets `render_as_batch=True`, so `ALTER TABLE` emits
  a table-rewrite strategy that works on SQLite.
- **No autogeneration:** this repo has no SQLAlchemy models; every migration is
  written by hand using `op.execute(...)` or typed helpers like
  `op.add_column`, `op.create_table`, etc.
- **No destructive migrations without review:** if a migration deletes a column
  or drops a table, the PR must be explicit about it.

## Bootstrap note

On first run against an existing DB already at legacy `PRAGMA user_version = 2`,
stamp Alembic to a baseline before applying new migrations:

```bash
uv run alembic stamp head
```

This tells Alembic that the schema is up-to-date as of the baseline version.
