"""Optional API data may degrade, but the failure must remain observable."""
from contextlib import contextmanager
import importlib


def test_voice_catalog_db_failure_keeps_shape_and_warns(monkeypatch, caplog):
    openai_compat = importlib.import_module("api.routers.openai_compat")
    db = importlib.import_module("core.db")
    @contextmanager
    def broken_db():
        raise OSError("database unavailable")
        yield

    monkeypatch.setattr(db, "db_conn", broken_db)
    monkeypatch.setattr("services.tts_backend.list_backends", lambda: [])
    with caplog.at_level("WARNING", logger="omnivoice.openai"):
        result = openai_compat.list_voices()

    assert set(result) == {"voices", "engines"}
    assert result["voices"]
    assert "built-in aliases only" in caplog.text


def test_notification_probe_failures_keep_shape_and_warn(monkeypatch, caplog):
    system = importlib.import_module("api.routers.system")
    run_sentinel = importlib.import_module("core.run_sentinel")
    monkeypatch.setattr(
        run_sentinel,
        "newest_record",
        lambda: (_ for _ in ()).throw(OSError("record unavailable")),
    )
    monkeypatch.setattr(
        system,
        "_crashed_last_session",
        lambda: (_ for _ in ()).throw(OSError("log unavailable")),
    )
    with caplog.at_level("WARNING"):
        result = system.system_notifications()

    assert set(result) == {"notifications", "count"}
    assert result["count"] == len(result["notifications"])
    assert "Previous-run crash record could not be checked" in caplog.text
    assert "Previous-session crash log could not be checked" in caplog.text
