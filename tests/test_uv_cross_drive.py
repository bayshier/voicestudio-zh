"""Non-system-drive install class (Discord report, tarbol6457).

uv keeps its wheel cache and managed Pythons on the SYSTEM drive
(%LOCALAPPDATA%\\uv, ~/.cache/uv). When the install target — engine venvs
under DATA_DIR, the managed app env — lives on a different volume (D:-drive
install, portable mode), every wheel is downloaded and unpacked on the system
drive first and then cross-volume COPIED into the venv: the system drive
silently needs as much space as the whole install and ENOSPC-es even though
the user pointed the install at another drive precisely because C: was tight.

The fix co-locates UV_CACHE_DIR / UV_PYTHON_INSTALL_DIR with the install
volume via ``services.sidecar_install.uv_subprocess_env`` (Python side) and
``setup.rs::uv_env_overrides_for`` (Tauri bootstrap side — covered by cargo
tests). These tests pin the Python half and guard against future engine
bootstraps regressing to environment-inheriting uv calls.
"""
import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

from services import sidecar_install as si

_REPO = Path(__file__).resolve().parents[1]


# ── volume probing ─────────────────────────────────────────────────────────

def test_nearest_existing_walks_up_to_a_real_dir(tmp_path):
    ghost = tmp_path / "no" / "such" / "dir"
    assert si._nearest_existing(ghost) == tmp_path
    assert si._nearest_existing(tmp_path) == tmp_path


def test_same_volume_true_for_same_dir_and_on_probe_failure(tmp_path, monkeypatch):
    assert si._same_volume(tmp_path, tmp_path / "not-created-yet")
    # Probe failure must err on True → no env override → default installs
    # can never change behavior because of a stat hiccup.
    monkeypatch.setattr(si.os, "stat", _raise_oserror)
    assert si._same_volume(tmp_path, tmp_path)


def _raise_oserror(*_a, **_k):
    raise OSError("no stat for you")


def test_same_volume_false_across_devices(tmp_path, monkeypatch):
    """Simulate two mount points by faking st_dev per path prefix."""
    d_drive = tmp_path / "d_drive"
    c_drive = tmp_path / "c_drive"
    d_drive.mkdir()
    c_drive.mkdir()
    real_stat = os.stat

    def fake_stat(p, *a, **k):
        res = real_stat(p, *a, **k)

        class _S:
            st_dev = 111 if str(p).startswith(str(d_drive)) else 222

            def __getattr__(self, name):
                return getattr(res, name)

        return _S()

    monkeypatch.setattr(si.os, "stat", fake_stat)
    assert not si._same_volume(d_drive, c_drive)
    assert si._same_volume(c_drive, c_drive)


# ── uv_subprocess_env ──────────────────────────────────────────────────────

def test_uv_env_is_none_on_the_default_cache_volume(tmp_path, monkeypatch):
    """Same volume as uv's default cache → inherit env untouched (default
    installs stay byte-identical)."""
    monkeypatch.delenv("UV_CACHE_DIR", raising=False)
    monkeypatch.setattr(si, "_default_uv_cache_root", lambda: tmp_path / "uv")
    assert si.uv_subprocess_env(tmp_path / "engines") is None


def test_uv_env_colocates_cache_on_a_foreign_volume(tmp_path, monkeypatch):
    monkeypatch.delenv("UV_CACHE_DIR", raising=False)
    monkeypatch.delenv("UV_PYTHON_INSTALL_DIR", raising=False)
    monkeypatch.setenv("OMNIVOICE_SENTINEL", "keep-me")
    monkeypatch.setattr(si, "_same_volume", lambda a, b: False)

    engines = tmp_path / "engines"
    env = si.uv_subprocess_env(engines)
    assert env is not None
    assert env["UV_CACHE_DIR"] == str(engines / ".uv-cache")
    assert env["UV_PYTHON_INSTALL_DIR"] == str(engines / ".uv-python")
    # A copy of the parent env, not a from-scratch dict — uv still needs
    # PATH, proxies, HF/UV mirrors, etc.
    assert env["OMNIVOICE_SENTINEL"] == "keep-me"


def test_uv_env_respects_user_pinned_cache_dir(tmp_path, monkeypatch):
    """An explicit UV_CACHE_DIR is the user's call — never overridden, even
    cross-volume. But the variables are independent (#1189 review): pinning
    the cache must not leave uv's managed-Python downloads on the system
    drive, so the unset UV_PYTHON_INSTALL_DIR is still co-located."""
    monkeypatch.setenv("UV_CACHE_DIR", str(tmp_path / "my-cache"))
    monkeypatch.delenv("UV_PYTHON_INSTALL_DIR", raising=False)
    monkeypatch.setattr(si, "_same_volume", lambda a, b: False)
    env = si.uv_subprocess_env(tmp_path / "engines")
    assert env is not None
    assert env["UV_CACHE_DIR"] == str(tmp_path / "my-cache")
    assert env["UV_PYTHON_INSTALL_DIR"] == str(tmp_path / "engines" / ".uv-python")


def test_uv_env_is_none_when_both_vars_pinned(tmp_path, monkeypatch):
    """Both pinned → nothing left to override → inherit env untouched."""
    monkeypatch.setenv("UV_CACHE_DIR", str(tmp_path / "my-cache"))
    monkeypatch.setenv("UV_PYTHON_INSTALL_DIR", str(tmp_path / "my-pythons"))
    monkeypatch.setattr(si, "_same_volume", lambda a, b: False)
    assert si.uv_subprocess_env(tmp_path / "engines") is None


def test_uv_env_respects_user_pinned_python_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("UV_CACHE_DIR", raising=False)
    monkeypatch.setenv("UV_PYTHON_INSTALL_DIR", str(tmp_path / "my-pythons"))
    monkeypatch.setattr(si, "_same_volume", lambda a, b: False)
    env = si.uv_subprocess_env(tmp_path / "engines")
    assert env is not None
    assert env["UV_CACHE_DIR"] == str(tmp_path / "engines" / ".uv-cache")
    assert env["UV_PYTHON_INSTALL_DIR"] == str(tmp_path / "my-pythons")


def test_default_uv_cache_root_is_absolute():
    root = si._default_uv_cache_root()
    assert root.is_absolute()
    assert root.name == "uv"


# ── the env actually reaches uv subprocesses ───────────────────────────────

def _job():
    from collections import deque
    return {"engine_id": "test", "log": deque(maxlen=50)}


def test_run_logged_forwards_env_to_the_child():
    job = _job()
    rc = si._run_logged(
        job,
        [sys.executable, "-c",
         "import os; print('CACHE=' + os.environ.get('UV_CACHE_DIR', 'MISSING'))"],
        timeout=30,
        env={**os.environ, "UV_CACHE_DIR": "X-DRIVE-CACHE"},
    )
    assert rc == 0
    assert any("CACHE=X-DRIVE-CACHE" in line for line in job["log"])


def test_run_logged_env_none_inherits_parent_environment(monkeypatch):
    monkeypatch.setenv("OMNIVOICE_INHERIT_PROBE", "inherited")
    job = _job()
    rc = si._run_logged(
        job,
        [sys.executable, "-c",
         "import os; print(os.environ.get('OMNIVOICE_INHERIT_PROBE', 'MISSING'))"],
        timeout=30,
    )
    assert rc == 0
    assert any("inherited" in line for line in job["log"])


def test_sidecar_create_venv_passes_colocation_env(monkeypatch, tmp_path):
    """_step_create_venv must hand uv_subprocess_env's result to _run_logged."""
    sentinel = {"UV_CACHE_DIR": "sentinel-cache"}
    seen = {}

    spec = si.SidecarSpec(
        engine_id="probe-engine",
        display_name="Probe",
        repo_url="https://example.invalid/repo.git",
        tarball_url="https://example.invalid/tar.gz",
        checkout_dirname="checkout",
        env_var="OMNIVOICE_PROBE_DIR",
        probe_module="probe",
        required_bytes=1,
    )
    monkeypatch.setattr(si, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(si, "_locate_uv", lambda: "uv")
    monkeypatch.setattr(si, "uv_subprocess_env", lambda parent: dict(sentinel, parent=str(parent)))

    def fake_run_logged(job, argv, *, timeout, env=None):
        seen["argv"] = argv
        seen["env"] = env
        # satisfy the post-condition: create the venv python
        py = si._venv_python(si.managed_checkout(spec) / ".venv")
        py.parent.mkdir(parents=True, exist_ok=True)
        py.write_text("")
        return 0

    monkeypatch.setattr(si, "_run_logged", fake_run_logged)
    job = {"engine_id": spec.engine_id, "log": __import__("collections").deque(maxlen=50),
           "steps": [{"id": "create_venv", "state": "pending", "detail": None}]}
    si._step_create_venv(spec, job)

    assert seen["argv"][0] == "uv"
    assert seen["env"]["UV_CACHE_DIR"] == "sentinel-cache"
    # The cache parent is the SHARED engines root, so all sidecars reuse one cache.
    assert seen["env"]["parent"] == str(Path(str(tmp_path)) / "engines")


# ── recurrence guard: every engine-bootstrap uv call must pass env= ────────

def _engine_bootstrap_files():
    return sorted((_REPO / "backend" / "engines").glob("*/bootstrap.py"))


def test_engine_bootstraps_exist():
    assert len(_engine_bootstrap_files()) >= 4  # indextts, dots, confucius4, moss


@pytest.mark.parametrize("path", _engine_bootstrap_files(), ids=lambda p: p.parent.name)
def test_every_bootstrap_uv_call_passes_env(path):
    """Every subprocess call inside _bootstrap_engines_venv must pass env=.

    Those are the uv venv / uv pip install calls: forgetting env= silently
    reintroduces the cross-drive cache blowup for the next engine. If a new
    bootstrap legitimately needs an inheriting call inside that function,
    pass env=None explicitly to make the decision visible.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_bootstrap_engines_venv":
            for call in ast.walk(node):
                if not isinstance(call, ast.Call):
                    continue
                fn = call.func
                is_subprocess_run = (
                    isinstance(fn, ast.Attribute) and fn.attr in ("run", "Popen", "check_call", "check_output")
                    and isinstance(fn.value, ast.Name) and fn.value.id == "subprocess"
                )
                if is_subprocess_run and not any(kw.arg == "env" for kw in call.keywords):
                    offenders.append(f"{path.parent.name}:{call.lineno}")
    assert not offenders, (
        "subprocess calls in _bootstrap_engines_venv without env= "
        f"(cross-drive uv cache class): {offenders}"
    )
