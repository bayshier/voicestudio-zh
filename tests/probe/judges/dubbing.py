"""L4 dubbing judges — verify the dubbing pipeline's structural correctness.

Dubbing adds timing + cross-modal constraints on top of TTS. The reliably
automatable checks (per the research) are: per-segment duration ratio (the core
dubbing problem — the dub overruns/underruns the source because languages differ
in information density), output language identification, and export-format
structural validity. Translation *quality* is not an audio problem and stays out
of scope. These judges are deterministic; language-ID is pluggable and skips
without a detector.
"""

from __future__ import annotations

import re
from typing import Any, Protocol

from ..spec import JudgeResult


def _seg_durations(seg: dict) -> tuple[float, float]:
    """(source, dubbed) durations from a segment dict, computing source from
    start/end when not given explicitly."""
    src = seg.get("source")
    if src is None and "start" in seg and "end" in seg:
        src = float(seg["end"]) - float(seg["start"])
    return float(src or 0.0), float(seg.get("dubbed", 0.0))


def segments_duration_ratio(segments: list, min_ratio: float = 0.5, max_ratio: float = 1.6) -> JudgeResult:
    """Each dubbed segment's duration must stay within [min,max]× its source —
    catches dub tracks that drift badly out of sync with the original.

    Fails when zero segments are validated (all durations missing/zero/negative)
    because a vacuously-true pass would hide an empty or corrupt segment list.
    """
    outliers = []
    validated = 0
    for i, seg in enumerate(segments or []):
        src, dub = _seg_durations(seg)
        if src <= 0:
            continue
        validated += 1
        ratio = dub / src
        if not (min_ratio <= ratio <= max_ratio):
            outliers.append(f"#{i}={ratio:.2f}")
    if validated == 0:
        return JudgeResult(
            name="segments_duration_ratio",
            passed=False,
            measured=0,
            detail=f"no segments with valid (>0) source duration in {len(segments or [])} segment(s) — "
            "nothing was actually validated",
        )
    ok = not outliers
    return JudgeResult(
        name="segments_duration_ratio",
        passed=ok,
        measured=len(outliers),
        detail=f"all {validated} segment(s) within [{min_ratio}, {max_ratio}]x"
        if ok else f"out-of-band ratios: {', '.join(outliers)}",
    )


_SRT_TS = re.compile(r"\d{2}:\d{2}:\d{2},\d{3}\s*-->\s*\d{2}:\d{2}:\d{2},\d{3}")
_VTT_TS = re.compile(r"\d{2}:\d{2}:\d{2}\.\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}\.\d{3}")


def srt_well_formed(text: str) -> JudgeResult:
    has_index = bool(re.search(r"(?m)^\s*\d+\s*$", text or ""))
    cues = len(_SRT_TS.findall(text or ""))
    ok = has_index and cues > 0
    return JudgeResult(
        name="srt_well_formed",
        passed=ok,
        measured=cues,
        detail=f"{cues} SRT cue(s) with HH:MM:SS,mmm timing" if ok else "no valid SRT cues/index",
    )


def vtt_well_formed(text: str) -> JudgeResult:
    starts = (text or "").lstrip().startswith("WEBVTT")
    cues = len(_VTT_TS.findall(text or ""))
    ok = starts and cues > 0
    return JudgeResult(
        name="vtt_well_formed",
        passed=ok,
        measured=cues,
        detail=f"WEBVTT + {cues} cue(s) with HH:MM:SS.mmm timing" if ok else "missing WEBVTT header or cues",
    )


def archive_has(names: list, patterns: list) -> JudgeResult:
    """Every pattern must match at least one entry name in the export archive."""
    names = list(names or [])
    missing = [p for p in patterns if not any(p in n for n in names)]
    return JudgeResult(
        name="archive_has",
        passed=not missing,
        measured=len(names),
        detail=f"archive has entries matching {patterns}" if not missing
        else f"archive missing patterns {missing} (have {names[:6]}...)",
    )


class LangDetector(Protocol):
    def detect(self, path: str) -> str: ...


def output_language_is(audio: str, expected: str, detector: LangDetector | None = None) -> JudgeResult:
    """Confirm the dubbed track is actually in the target language. Pluggable
    (Whisper detect_language); SKIPS without a detector. Note: language-ID errs
    on heavily-accented or very short speech — use whole segments."""
    if detector is None:
        return JudgeResult(name="output_language_is", passed=None,
                           detail="skipped: no language detector wired (inject a Whisper detect_language backend)")
    got = detector.detect(audio)
    ok = got == expected
    return JudgeResult(name="output_language_is", passed=ok, measured=got,
                       detail=f"detected {got!r} (expected {expected!r})")
