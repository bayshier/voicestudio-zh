import pytest
from fastapi import HTTPException


@pytest.mark.asyncio
async def test_unknown_remote_target_never_falls_through_to_local_install(monkeypatch):
    from api.routers.setup import download
    from worker import routing

    monkeypatch.setattr(routing, "decide", lambda: routing.Decision(remote=False))
    request = download.InstallModelRequest(
        repo_id=download.KNOWN_MODELS[0]["repo_id"],
        target="missing-worker",
    )

    with pytest.raises(HTTPException) as excinfo:
        await download.install_model(request)

    assert excinfo.value.status_code == 409
    assert "selected GPU target changed" in excinfo.value.detail
