"""VideoAI — generator video AI 100% gratis (tanpa API key, tanpa biaya)."""
from .config import (  # noqa: F401
    ASSETS_DIR, CACHE_DIR, OUTPUT_DIR, PRESETS, VOICES, WORK_DIR,
)

__version__ = "1.0.0"
__all__ = [
    "ASSETS_DIR", "CACHE_DIR", "OUTPUT_DIR", "PRESETS", "VOICES", "WORK_DIR",
    "__version__", "generate_video", "VideoRequest", "VideoResult", "available_options",
]


def __getattr__(name):
    # Lazy import agar `python -c "import videoai"` tidak memuat Flask/edge-tts.
    if name in {"generate_video", "VideoRequest", "VideoResult", "available_options"}:
        from . import pipeline
        return getattr(pipeline, name)
    raise AttributeError(name)
