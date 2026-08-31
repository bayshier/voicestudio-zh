"""Audiobook multi-voice cast mapping (#1217).

The headline fix: ``[voice:NAME]`` used to be handed to ``_resolve_voice`` as if
NAME were a profile id — it never matched (profile ids are UUIDs), so every
``[voice:…]`` silently rendered in the engine default and a multi-voice book was
mono-voiced. A book now carries a ``voice_map`` (NAME → profile id); this suite
pins the resolution, the silent-default fix for unmapped names, exact-id
back-compat, and the CRITICAL cache-signature guard (remapping must re-render;
an absent map must keep today's byte-identical keys).

Engine + DB boundary stubbed throughout — no model loads, no GPU, no ffmpeg.
"""
import os

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

import torch

from services.audiobook import (
    Chapter,
    ExpressiveOptions,
    Span,
    voice_map_signature,
)

_PID = "11111111-2222-3333-4444-555555555555"


# ── voice_map_signature: the CRITICAL TRAP guard ────────────────────────────

def test_absent_or_empty_map_has_empty_signature():
    # An absent/empty map must be byte-identical to pre-#1217: no signature, so
    # no perturbation of any cache key — existing books never re-render.
    assert voice_map_signature(None) == ""
    assert voice_map_signature({}) == ""


def test_map_produces_a_signature_that_changes_with_content():
    a = voice_map_signature({"Mara": _PID})
    b = voice_map_signature({"Mara": "other-pid"})
    c = voice_map_signature({"Mara": _PID, "Cole": "pid2"})
    assert a and b and c
    assert len({a, b, c}) == 3
    # Order-independent (canonical JSON) — same map, same key.
    assert voice_map_signature({"Cole": "pid2", "Mara": _PID}) == c


# ── name → profile resolution through the resolve closure ───────────────────

def _generic_synth_recording(monkeypatch):
    """A generic backend + a recording ``_resolve_voice`` — returns (build, seen)
    where ``seen`` collects every profile id resolution was asked for."""
    import api.routers.audiobook as ab
    import services.tts_backend as tb
    from services.tts_backend import TTSBackend

    class _Fake(TTSBackend):
        id = "fake-cast-engine"
        display_name = "Fake Cast Engine (test)"
        gpu_compat = ("cpu",)

        @property
        def sample_rate(self):
            return 24000

        @property
        def supported_languages(self):
            return ["multi"]

        @classmethod
        def is_available(cls):
            return True, "ready"

        def generate(self, text, **kw):
            return torch.zeros(1, 2400)

    monkeypatch.setattr(tb, "active_backend_id", lambda: "fake-cast-engine")
    monkeypatch.setattr(tb, "get_backend_class", lambda _id: _Fake)

    seen = []

    def fake_resolve(pid):
        seen.append(pid)
        return {"ref_audio": None, "ref_text": None, "instruct": None, "seed": None}

    monkeypatch.setattr(ab, "_resolve_voice", fake_resolve)
    return ab, seen


def test_named_voice_resolves_through_the_map(monkeypatch):
    # [voice:Mara] with voice_map={"Mara": <pid>} must resolve to that profile.
    ab, seen = _generic_synth_recording(monkeypatch)
    info = ab._build_synth("default-pid", voice_map={"Mara": _PID})
    info["synth"]("hello", "Mara")
    assert seen == [_PID]


def test_unmapped_name_falls_back_to_default_voice(monkeypatch):
    # Without a map, a bare NAME is NOT a real profile id → must fall back to
    # default_voice (the silent-default bug fix), NOT be treated as a literal id.
    ab, seen = _generic_synth_recording(monkeypatch)
    monkeypatch.setattr(ab, "_voice_profile_exists", lambda _pid: False)
    info = ab._build_synth("default-pid", voice_map=None)
    info["synth"]("hello", "Mara")
    assert seen == ["default-pid"]


def test_exact_profile_id_still_resolves_as_itself(monkeypatch):
    # An unmapped token that IS a real profile id (someone passed an exact id,
    # e.g. a Stories span) must resolve unchanged — exact-id back-compat.
    ab, seen = _generic_synth_recording(monkeypatch)
    monkeypatch.setattr(ab, "_voice_profile_exists", lambda pid: pid == _PID)
    info = ab._build_synth("default-pid", voice_map=None)
    info["synth"]("hello", _PID)
    assert seen == [_PID]


def test_none_voice_uses_default_without_a_db_probe(monkeypatch):
    # A run with no [voice:] (None) resolves to default_voice and must never hit
    # the profile-existence DB probe.
    ab, seen = _generic_synth_recording(monkeypatch)

    def _boom(_pid):  # would fire only if None wrongly reached the probe
        raise AssertionError("None must not probe the DB")

    monkeypatch.setattr(ab, "_voice_profile_exists", _boom)
    info = ab._build_synth("default-pid", voice_map={"Mara": _PID})
    info["synth"]("hello", None)
    assert seen == ["default-pid"]


# ── chapter / segment / preview cache keys absorb the voice map ─────────────

_RESOLVE = lambda _vid: {  # noqa: E731
    "ref_audio": None, "ref_text": None, "instruct": None, "seed": None,
}


def _render_key(tmp_path, voice_map):
    """Render a one-span [voice:Mara] chapter with a stub synth (no models);
    return the content-addressed chapter cache key (the WAV basename)."""
    from api.routers.audiobook import _render_chapter_cached

    ch = Chapter(title="C", spans=[Span(voice_id="Mara", text="hello", pause_ms_after=0)])
    synth = lambda text, vid, speed=None: torch.zeros(2400)  # noqa: E731
    wav_path, *_ = _render_chapter_cached(
        ch, synth, 24000, "eng", _RESOLVE, str(tmp_path), None, None,
        ExpressiveOptions(), voice_map,
    )
    return os.path.basename(wav_path)


def test_chapter_cache_key_changes_when_the_map_changes(tmp_path):
    base = _render_key(tmp_path, None)                       # no map
    empty = _render_key(tmp_path, {})                        # empty map == no map
    a = _render_key(tmp_path, {"Mara": _PID})                # mapped one way
    b = _render_key(tmp_path, {"Mara": "other-pid"})         # remapped
    assert empty == base, "an absent/empty map must keep today's cache key"
    assert a != base, "adding a mapping must re-render"
    assert b != a, "remapping a voice must re-render"


def test_segment_extra_sig_absorbs_the_voice_map():
    # The inner segment cache keys on ``extra_sig`` — the same string the chapter
    # render folds the voice-map signature into. Two otherwise-identical segments
    # that differ only by mapping must land on distinct segment keys, and an
    # empty map must leave the key byte-identical to the no-map derivation.
    from services.longform_render import segment_cache_key

    def seg_extra(voice_map):
        vmap = voice_map_signature(voice_map)
        return f"\x00{vmap}" if vmap else ""

    def key(voice_map):
        return segment_cache_key("hello", sample_rate=24000, engine_id="eng",
                                 voice_id="Mara", extra_sig=seg_extra(voice_map))

    base = key(None)
    assert key({}) == base                       # empty map == today's key
    assert key({"Mara": _PID}) != base           # a mapping re-renders
    assert key({"Mara": _PID}) != key({"Mara": "other"})  # remap re-renders


# ── render / preview parity ─────────────────────────────────────────────────

def test_preview_and_render_requests_carry_and_key_on_the_same_map(tmp_path):
    from api.routers.audiobook import AudiobookPreviewRequest, AudiobookRequest

    vm = {"Mara": _PID, "Cole": "pid2"}
    r = AudiobookRequest(text="# A\n[voice:Mara] hi", voice_map=vm)
    p = AudiobookPreviewRequest(text="# A\n[voice:Mara] hi", voice_map=vm)
    assert r.voice_map == p.voice_map == vm
    # …and therefore the same chapter cache slot (preview warms what render reuses).
    assert _render_key(tmp_path, r.voice_map) == _render_key(tmp_path, p.voice_map)


def test_default_request_omits_the_map(tmp_path):
    from api.routers.audiobook import AudiobookRequest

    # An untouched request has no map → today's exact key (no re-render).
    assert AudiobookRequest(text="# A\nhi").voice_map is None
    assert _render_key(tmp_path, None) == _render_key(tmp_path, {})
