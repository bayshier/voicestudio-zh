from worker import capabilities


def test_downloaded_uses_shared_cache_helpers(monkeypatch):
    monkeypatch.setattr(capabilities, "repo_ids_for", lambda _entry: ["org/model"])
    monkeypatch.setattr(capabilities, "_resident_engine_ids", lambda: set())
    monkeypatch.setattr("core.device_caps.detect_host_caps", lambda: None)
    monkeypatch.setattr("services.tts_backend.list_backends", lambda: [{
        "id": "omnivoice", "available": True, "routing_status": "accelerated"
    }])
    monkeypatch.setattr("api.routers.setup.models.is_cached", lambda _repo: False)

    row = capabilities.discover(include_unavailable=True)[0]

    assert row["downloaded"] is False
    assert row["repo_ids"] == ["org/model"]


def test_engine_without_a_catalog_repo_is_advertised_and_schedulable(monkeypatch):
    """Missing download metadata must not hide an already-working engine."""
    monkeypatch.setattr(capabilities, "repo_ids_for", lambda _entry: [])
    monkeypatch.setattr(capabilities, "_resident_engine_ids", lambda: set())
    monkeypatch.setattr("core.device_caps.detect_host_caps", lambda: None)
    monkeypatch.setattr("services.tts_backend.list_backends", lambda: [{
        "id": "omnivoice", "available": True, "routing_status": "accelerated"
    }])

    discovered = capabilities.discover()

    assert [row["engine"] for row in discovered] == ["omnivoice"]
    assert discovered[0]["repo_ids"] == []

    from worker.pool import ConnectedWorker
    from worker.registry import RemoteWorker

    worker = ConnectedWorker(
        record=RemoteWorker(
            id="worker-1",
            name="worker",
            key_id="key-1",
            public_key=b"key",
            capabilities=discovered,
            consent_granted_at=1.0,
        ),
        session=None,
        epoch=1,
        capacity=None,
        connected_at=1.0,
        last_heartbeat_at=1.0,
    )
    assert worker.record.schedulable is True
    assert worker.supports("omnivoice", "omnivoice:default", "tts") is True


def test_hf_downloadable_engine_always_names_a_repository(monkeypatch):
    """A positive missing-weights answer must carry an HF download target."""
    monkeypatch.setattr(capabilities, "repo_ids_for", lambda _entry: ["org/model"])
    monkeypatch.setattr(capabilities, "_resident_engine_ids", lambda: set())
    monkeypatch.setattr("core.device_caps.detect_host_caps", lambda: None)
    monkeypatch.setattr("services.tts_backend.list_backends", lambda: [{
        "id": "omnivoice", "available": True, "routing_status": "accelerated"
    }])
    monkeypatch.setattr("api.routers.setup.models.is_cached", lambda _repo: False)

    for row in capabilities.discover():
        if row["downloaded"] is False:
            assert row["repo_ids"], "HF-downloadable capabilities need a repository"


def test_download_probe_fails_open_when_cache_is_inconclusive(monkeypatch):
    monkeypatch.setattr("api.routers.setup.models.is_cached", lambda _repo: (_ for _ in ()).throw(OSError()))
    assert capabilities._downloaded(["user/managed-model"]) is True


def test_unavailable_engine_is_reported_when_requested(monkeypatch):
    monkeypatch.setattr(capabilities, "repo_ids_for", lambda _entry: [])
    monkeypatch.setattr(capabilities, "_resident_engine_ids", lambda: set())
    monkeypatch.setattr("core.device_caps.detect_host_caps", lambda: None)
    monkeypatch.setattr("services.tts_backend.list_backends", lambda: [{
        "id": "indextts2", "available": False, "routing_status": "unavailable"
    }])
    assert capabilities.discover() == []
    assert capabilities.discover(include_unavailable=True)[0]["engine"] == "indextts2"
