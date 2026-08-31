"""#1152 — export failures must be diagnosed honestly.

A Windows dub export failed with `[WinError 206] The filename or extension
is too long` at ffmpeg *spawn* time (the mux argv grows per track/segment
and can exceed the Windows 32,767-char CreateProcess limit). The catch-all
in dub_export concatenated that OS error with "Verify ffmpeg is installed…"
— telling a user with a working ffmpeg and a short F:\\video.mp4 output
path that their filename was too long and ffmpeg might be missing.

Class fix, two layers:
  * `explain_ffmpeg_failure` maps each failure mode to what actually
    happened — argv-too-long, ffmpeg unlaunchable, or ffmpeg-ran-and-failed
    — so no cause ever gets another cause's advice;
  * `externalize_long_filter_complex` moves an oversized -filter_complex
    graph (the dominant argv consumer: bed-mix/apad branches scale with
    track count) into a -filter_complex_script file so the spawn never
    hits the limit in the first place.
"""
import errno
import os
import sys

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from services.ffmpeg_utils import (  # noqa: E402
    explain_ffmpeg_failure,
    externalize_long_filter_complex,
)


class TestExplainFfmpegFailure:
    def test_argv_too_long_names_the_real_cause_not_ffmpeg_install(self):
        e = OSError(errno.ENAMETOOLONG, "The filename or extension is too long")
        msg = explain_ffmpeg_failure(e, "combine video + dubbed audio",
                                     cmd=["ffmpeg"] + ["x" * 100] * 400)
        assert "command line" in msg.lower()
        # The two misdiagnoses from the bug report must be gone:
        assert "installed" not in msg.lower()
        assert not msg.lower().startswith("the filename")

    def test_unlaunchable_ffmpeg_is_the_only_case_that_suggests_install(self):
        e = FileNotFoundError(errno.ENOENT, "No such file or directory: 'ffmpeg'")
        msg = explain_ffmpeg_failure(e, "combine video + dubbed audio")
        assert "ffmpeg -version" in msg or "installed" in msg.lower()

    def test_ffmpeg_ran_and_failed_shows_stderr_without_install_advice(self):
        e = Exception("Error opening input file dubbed_vi.wav: No such file")
        msg = explain_ffmpeg_failure(e, "combine video + dubbed audio")
        assert "dubbed_vi.wav" in msg
        assert "installed" not in msg.lower()


class TestExternalizeLongFilterComplex:
    def test_long_graph_moves_to_a_script_file(self, tmp_path):
        graph = ";".join(f"[{i}:a]apad[a{i}]" for i in range(2000))
        cmd = ["ffmpeg", "-i", "in.mp4", "-filter_complex", graph, "out.mp4"]
        out, script = externalize_long_filter_complex(cmd, limit=1000, tmp_dir=str(tmp_path))
        assert script is not None and os.path.isfile(script)
        with open(script, encoding="utf-8") as f:
            assert f.read() == graph
        i = out.index("-filter_complex_script")
        assert out[i + 1] == script
        assert "-filter_complex" not in [a for a in out if a != "-filter_complex_script"]
        # Everything else preserved in order.
        assert out[0:3] == ["ffmpeg", "-i", "in.mp4"]
        assert out[-1] == "out.mp4"

    def test_short_command_is_untouched(self, tmp_path):
        cmd = ["ffmpeg", "-i", "in.mp4", "-filter_complex", "[0:a]apad", "out.mp4"]
        out, script = externalize_long_filter_complex(cmd, limit=100000, tmp_dir=str(tmp_path))
        assert out == cmd
        assert script is None

    def test_command_without_filter_complex_is_untouched(self, tmp_path):
        cmd = ["ffmpeg", "-i", "in.mp4"] + ["-map", "0"] * 3000 + ["out.mp4"]
        out, script = externalize_long_filter_complex(cmd, limit=1000, tmp_dir=str(tmp_path))
        assert out == cmd
        assert script is None
