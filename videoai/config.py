"""Konfigurasi global untuk aplikasi VideoAI."""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
ASSETS_DIR = BASE_DIR / "assets"
CACHE_DIR = ASSETS_DIR / "cache"
OUTPUT_DIR = BASE_DIR / "output"
WORK_DIR = BASE_DIR / "work"
MUSIC_DIR = ASSETS_DIR / "music"

for _d in (ASSETS_DIR, CACHE_DIR, OUTPUT_DIR, WORK_DIR, MUSIC_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------- providers
POLLINATIONS_IMAGE_ENDPOINT = "https://image.pollinations.ai/prompt/{prompt}"
POLLINATIONS_TEXT_ENDPOINT = "https://text.pollinations.ai/openai"
DEFAULT_IMAGE_MODEL = os.getenv("VIDEOAI_IMAGE_MODEL", "flux")
DEFAULT_TEXT_MODEL = os.getenv("VIDEOAI_TEXT_MODEL", "openai")

# Sana/FLUX mengembalikan maksimum ~1024 px pada sisi terpanjang.
IMAGE_MAX_SIDE = 1024

# ---------------------------------------------------------------- video
FPS = 30
PRESETS = {
    "16:9": (1920, 1080),
    "9:16": (1080, 1920),
    "1:1": (1080, 1080),
}
QUALITY = {
    "draft": {"crf": 23, "preset": "veryfast", "overscan": 1.15},
    "high": {"crf": 18, "preset": "medium", "overscan": 1.30},
    "ultra": {"crf": 15, "preset": "slow", "overscan": 1.40},
}

TRANSITION_SECONDS = 0.55
SCENE_TAIL_PAD = 0.9       # ekstra jeda di akhir narasi tiap scene
NARRATION_LEAD = 0.35      # jeda sebelum narasi mulai di dalam scene
MUSIC_VOLUME = 0.16        # volume musik latar relatif (0..1)

# ---------------------------------------------------------------- voices
VOICES = {
    "id": {
        "label": "Bahasa Indonesia",
        "male": "id-ID-ArdiNeural",
        "female": "id-ID-GadisNeural",
    },
    "en": {
        "label": "English",
        "male": "en-US-AndrewMultilingualNeural",
        "female": "en-US-EmmaMultilingualNeural",
    },
    "ja": {
        "label": "日本語",
        "male": "ja-JP-KeitaNeural",
        "female": "ja-JP-NanamiNeural",
    },
    "es": {
        "label": "Español",
        "male": "es-ES-AlvaroNeural",
        "female": "es-ES-ElviraNeural",
    },
    "ar": {
        "label": "العربية",
        "male": "ar-SA-HamedNeural",
        "female": "ar-SA-ZariyahNeural",
    },
    "ms": {
        "label": "Bahasa Melayu",
        "male": "ms-MY-OsmanNeural",
        "female": "ms-MY-YasminNeural",
    },
}

DEFAULT_LANGUAGE = "id"
DEFAULT_SCENE_COUNT = 6
MIN_SCENES, MAX_SCENES = 1, 12
