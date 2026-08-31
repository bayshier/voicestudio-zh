# VoiceStudio: CosyVoice Engine

CosyVoice is an optional multilingual TTS backend for zero-shot voice cloning
and instructed speech. VoiceStudio currently exposes it as an in-process
adapter rather than an isolated engine sidecar.

## Downloaded weights and an available engine are different states

The Model Catalogue tracks model weights separately from engine runtime
availability. A downloaded
`FunAudioLLM/Fun-CosyVoice3-0.5B-2512` cache means the model files reached the
machine. It does not prove that the VoiceStudio backend can import and run
CosyVoice.

The current readiness check requires the same Python interpreter that runs the
VoiceStudio backend to import:

```python
from cosyvoice.cli.cosyvoice import AutoModel
```

It then loads the directory named by `OMNIVOICE_COSYVOICE_MODEL`, or defaults
to `pretrained_models/Fun-CosyVoice3-0.5B`. That path must resolve to the usable
model directory, not only the parent Hugging Face cache directory.

## Packaged builds

The current packaged app does not provide a one-click CosyVoice runtime
installer. Downloading the model weights from Model Catalogue does not install
the CosyVoice Python runtime or SoX. Re-downloading the weights will not repair
a missing runtime.

Do not install CosyVoice requirements into an unrelated Python environment.
VoiceStudio will continue to report the engine unavailable because its backend
cannot import packages from that environment.

Use **Model Catalogue > Engines > CosyVoice > Re-check** to inspect the runtime
reason. If the row remains unavailable, use another engine that reports ready
or collect the diagnostics below. The packaged release has no supported direct
CosyVoice runtime installation path today.

## Source builds and existing installations

The upstream CosyVoice project recommends its own Python 3.10 Conda environment
and SoX. VoiceStudio does not currently bridge that separate interpreter to its
in-process adapter. Installing upstream dependency pins into VoiceStudio's
shared backend environment can also conflict with other engines.

Existing source installations remain usable when the VoiceStudio backend's
interpreter can already import `cosyvoice.cli.cosyvoice.AutoModel`. Keep a
working installation in place. Before starting VoiceStudio, set
`OMNIVOICE_COSYVOICE_MODEL` to its usable model directory through the source
checkout's environment or project `.env` file.

For upstream setup details, read the
[official CosyVoice installation guide](https://github.com/QwenAudio/CosyVoice#install).
Those steps create a standalone CosyVoice environment. They do not turn the
current packaged VoiceStudio app into a CosyVoice installer.

## Diagnose an unavailable engine

Include these facts in a support question or bug report:

1. The exact VoiceStudio version.
2. The full reason under **Model Catalogue > Engines > CosyVoice** after
   selecting **Re-check**.
3. The CosyVoice lines from **Settings > Logs > Backend** immediately after
   that check.
4. On Windows, the output of `where.exe sox` in PowerShell.

These facts distinguish a downloaded-model state from a missing Python runtime,
missing SoX executable, or incorrect model directory. Do not delete a model
cache or reinstall dependencies until the log identifies which state failed.

The public report that exposed the misleading installed state is
[Discussion 1631](https://github.com/debpalash/VoiceStudio/discussions/1631).
