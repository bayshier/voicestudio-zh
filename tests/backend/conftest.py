"""Shared cleanup for tests/backend — undo sys.modules surgery.

Several files in this tree (test_perf_settings.py, test_engine_spawn_token.py,
api/test_engines_route_shape.py, services/test_token_resolver.py, …) purge
``core`` / ``api`` / ``services`` from ``sys.modules`` and re-import them under
a per-test, monkeypatched ``OMNIVOICE_DATA_DIR``. monkeypatch restores the ENV
at teardown, but the re-imported modules stay cached — bound to the now-dead
tmp_path (``core.config`` freezes DB_PATH/VOICES_DIR at import time). Any
later test that lazily resolves those modules (e.g. a route handler doing
``from services import x`` at request time) then reads/writes a data dir that
no other part of that test uses: in combined ``pytest tests/ backend/tests/``
runs this broke backend/tests' personas import (voice file written into the
poisoned VOICES_DIR) and audiobook resume (job seeded in one DB, endpoint
reading another). CI's isolated invocations never see it; local combined runs
do.

The autouse teardown below re-purges after every test here, so the next
consumer re-imports against the RESTORED env. It deliberately mirrors the
setup-side purge condition used by those files — keep the two in sync, and
keep the bare package names ("api", "services", "core"): a surviving stale
package object still holds attribute bindings to stale submodules, which
splits ``from services import x`` (package attr, stale) from
``from services.x import y`` (fresh re-import).
"""
import sys

import pytest


def purge_backend_modules() -> None:
    for mod in list(sys.modules):
        if (
            mod in ("main", "core", "api", "services")
            or mod.startswith("core.")
            or mod.startswith("api.")
            or mod.startswith("services.")
        ):
            sys.modules.pop(mod, None)


@pytest.fixture(autouse=True)
def _repurge_backend_modules_after_module_surgery():
    yield
    purge_backend_modules()
