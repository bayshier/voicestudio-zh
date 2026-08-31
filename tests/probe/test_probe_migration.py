"""DB migration — boot against a copy of the checked-in omnivoice_data fixture so
alembic runs its UPGRADE path on existing user data (backward-compat constraint).
Subprocess-isolated; the model load is short-circuited."""

from __future__ import annotations

import os

from . import env
from . import spec as probe_spec

_SPEC = os.path.join(os.path.dirname(__file__), "specs", "migration.probe.yaml")


def test_migration_upgrades_existing_data(probe_report):
    spec = probe_spec.load_spec(_SPEC)
    with env.seeded_data_dir() as data_dir:
        context = env.capture_first_run(data_dir)
        # Run judges INSIDE the with-block: the migration spec's path_exists check
        # must see the seeded DB before the temp data dir is torn down on exit.
        results = probe_spec.run_judges(spec, context)
    probe_report.record(spec, results)
    assert probe_spec.blocking_failures(results) == [], "\n".join(str(r) for r in results)
    # Data integrity: the existing DB file must still be present after migration
    # (db_path is set even for pre-existing files; db_created would be False here
    # since the seeded fixture already has a DB — we want presence, not creation).
    assert context["db_path"], "DB file not found after migration — data may have been lost"
