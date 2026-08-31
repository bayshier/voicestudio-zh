import warnings
from importlib.metadata import PackageNotFoundError, version

warnings.filterwarnings("ignore", module="torchaudio")
warnings.filterwarnings(
    "ignore",
    category=SyntaxWarning,
    message="invalid escape sequence",
    module="pydub.utils",
)
warnings.filterwarnings(
    "ignore",
    category=FutureWarning,
    module="torch.distributed.algorithms.ddp_comm_hooks",
)

try:
    __version__ = version("omnivoice")
except PackageNotFoundError:
    __version__ = "0.0.0"

__all__ = ["OmniVoice", "OmniVoiceConfig", "OmniVoiceGenerationConfig"]

# The model exports are resolved lazily (PEP 562). Importing them here made
# `omnivoice` an all-or-nothing package: `backend/api/routers/profiles.py` asks
# only for two pure-stdlib helpers from `omnivoice.utils.voice_design`, and got
# torch + torchaudio + transformers + the full model definition as a side
# effect. Any breakage in that stack — a torchaudio transformers can't detect,
# a flex_attention symbol a torch version doesn't have — then killed the entire
# backend at import time, before FastAPI existed to classify the error: TTS,
# dubbing, ASR and Settings all dead, with only a uvicorn traceback to go on
# (#1229). Deferred, the same breakage surfaces inside the request that
# actually needs a model, where `core.failure.classify()` attaches a repair
# hint and everything else keeps working.
#
# `from omnivoice import OmniVoice` and `omnivoice.OmniVoice` behave exactly as
# before; only the *timing* of the heavy import changes.


def __getattr__(name):
    if name in __all__:
        from omnivoice.models import omnivoice as _m

        return getattr(_m, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted([*globals(), *__all__])
