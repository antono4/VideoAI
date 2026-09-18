"""Penyedia layanan AI gratis (tanpa API key).

- Gambar  : Pollinations.ai  (model FLUX / SANA)
- Teks    : Pollinations.ai  (OpenAI-compatible endpoint, GPT-OSS 20B)
- Suara   : edge-tts         (Microsoft Neural TTS)
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, replace as dc_replace
from pathlib import Path
from typing import Any, Callable, Iterable

from . import config

log = logging.getLogger("videoai.providers")

USER_AGENT = "VideoAI/1.0 (+free-ai-video-generator)"

ProgressFn = Callable[[str, float], None]


class ProviderError(RuntimeError):
    """Kesalahan dari layanan AI eksternal."""


def _noop(_msg: str, _pct: float) -> None:  # pragma: no cover
    pass


# =====================================================================
# HTTP helper
# =====================================================================
def _request(url: str, *, data: bytes | None = None, headers: dict | None = None,
             timeout: int = 180, retries: int = 4, backoff: float = 2.0) -> bytes:
    """GET/POST dengan retry eksponensial untuk 429/5xx."""
    hdrs = {"User-Agent": USER_AGENT}
    if headers:
        hdrs.update(headers)
    last: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=data, headers=hdrs)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            last = exc
            # 4xx selain 429 tidak akan sembuh dengan retry.
            if exc.code != 429 and exc.code < 500:
                raise ProviderError(f"HTTP {exc.code} dari {url.split('?')[0]}") from exc
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            wait = backoff * (2 ** attempt) + random.uniform(0, 1.5)
            if retry_after:
                try:
                    wait = max(wait, min(120.0, float(retry_after)))
                except ValueError:
                    pass
            log.warning("HTTP %s, retry dalam %.1fs (%d/%d)", exc.code, wait, attempt + 1, retries)
            time.sleep(wait)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = exc
            wait = backoff * (2 ** attempt) + random.uniform(0, 1.5)
            log.warning("Koneksi gagal (%s), retry dalam %.1fs", exc, wait)
            time.sleep(wait)
    raise ProviderError(f"Gagal menghubungi layanan setelah {retries} percobaan: {last}")


# =====================================================================
# 1. GENERATOR GAMBAR
# =====================================================================
@dataclass
class ImageRequest:
    prompt: str
    width: int
    height: int
    seed: int
    model: str = config.DEFAULT_IMAGE_MODEL
    negative: str = ""
    enhance: bool = True


def _cache_key(req: ImageRequest) -> str:
    raw = json.dumps({
        "p": req.prompt.strip().lower(), "w": req.width, "h": req.height,
        "s": req.seed, "m": req.model, "n": req.negative,
    }, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def build_image_url(req: ImageRequest) -> str:
    params = {
        "width": req.width,
        "height": req.height,
        "seed": req.seed,
        "model": req.model,
        "nologo": "true",
        "enhance": "true" if req.enhance else "false",
        "safe": "false",
    }
    if req.negative:
        params["negative"] = req.negative
    return f"{config.POLLINATIONS_IMAGE_ENDPOINT.format(prompt=urllib.parse.quote(req.prompt))}?{urllib.parse.urlencode(params)}"


def generate_image(req: ImageRequest, *, use_cache: bool = True,
                   fallback_models: tuple[str, ...] = ("flux", "sana", "turbo")) -> Path:
    """Unduh satu gambar AI gratis, dengan cache disk dan fallback model.

    Jika model utama sedang dibatasi/di-rate-limit, model lain dicoba; ini
    membuat hasil tetap ada meski salah satu backend gratis sedang sibuk.
    """
    key = _cache_key(req)
    target = config.CACHE_DIR / f"img_{key}.jpg"
    if target.exists() and target.stat().st_size > 2048 and use_cache:
        return target

    models = [req.model] + [m for m in fallback_models if m != req.model]
    errors: list[str] = []
    for model in models:
        candidate = dc_replace(req, model=model)
        try:
            payload = _request(build_image_url(candidate), timeout=240, retries=3, backoff=2.5)
        except ProviderError as exc:
            errors.append(f"{model}: {exc}")
            continue
        if len(payload) < 1024 or payload[:3] != b"\xff\xd8\xff":
            errors.append(f"{model}: respons bukan JPEG")
            continue
        tmp = target.with_suffix(".part")
        tmp.write_bytes(payload)
        tmp.replace(target)
        if model != req.model:
            log.info("Gambar dibuat dengan model cadangan '%s'.", model)
        return target

    raise ProviderError("Gagal membuat gambar (" + " | ".join(errors[:3]) + ")")


def generate_images_batch(
    requests: Iterable[ImageRequest],
    *,
    progress: ProgressFn = _noop,
    max_workers: int = 1,
    on_error: str = "fallback",
    pace_seconds: float = 1.5,
    attempts_per_image: int = 3,
) -> list[Path | None]:
    """Buat beberapa gambar dengan pacing dan retry per gambar.

    Pollinations membatasi laju secara agresif untuk pemakaian anonim, jadi
    permintaan dijalankan berurutan dengan jeda pendek dan beberapa percobaan
    mandiri per gambar. Satu gambar gagal tidak menggagalkan scene lain.
    """
    requests = list(requests)
    total = len(requests)
    results: list[Path | None] = [None] * total
    errors: list[str] = []
    started = time.time()

    for idx, req in enumerate(requests):
        path: Path | None = None
        last_err: str | None = None
        for attempt in range(attempts_per_image):
            try:
                path = generate_image(req)
                break
            except Exception as exc:  # noqa: BLE001
                last_err = str(exc)
                # Variasi prompt/seed kecil kadang menembus batas laju.
                if attempt < attempts_per_image - 1:
                    jitter = random.uniform(1.0, 3.0) * (attempt + 1)
                    log.warning("Gambar %d gagal (%s); ulangi dalam %.1fs",
                                idx + 1, exc, jitter)
                    time.sleep(jitter)
                    req = dc_replace(req, seed=req.seed + 31337)
        if path is None:
            errors.append(f"scene {idx + 1}: {last_err}")
            log.error("Gambar #%d gagal permanen: %s", idx + 1, last_err)
        results[idx] = path

        progress(f"Gambar {idx + 1}/{total} siap", (idx + 1) / total)
        if idx < total - 1 and pace_seconds:
            time.sleep(pace_seconds)

    if errors and on_error == "raise" and all(r is None for r in results):
        raise ProviderError("Semua gambar gagal: " + "; ".join(errors[:3]))
    log.info("Gambar selesai: %d/%d dalam %.1fs",
             sum(1 for r in results if r), total, time.time() - started)
    return results



# =====================================================================
# 2. GENERATOR SKRIP / STORYBOARD (LLM gratis)
# =====================================================================
_SYSTEM_PROMPT = """You are a professional video scriptwriter and storyboard artist.
You always answer with a single valid JSON object and nothing else - no markdown, no commentary.
Image prompts must be in English, vivid, visual, cinematic, and describe ONLY what is visible
(subject, setting, light, mood, camera angle, lens). Never include text, letters, logos or
watermarks in image prompts."""


def _storyboard_user_prompt(topic: str, language: str, scenes: int, style: str,
                            tone: str, extra: str) -> str:
    return f"""Create a storyboard for a short video.

Topic: {topic}
Target number of scenes: {scenes}
Narration language: {language}
Visual style: {style}
Tone: {tone}
{f"Extra direction: {extra}" if extra else ""}

Rules:
- narration is the voice-over for that scene, written in the narration language,
  1-3 natural spoken sentences (roughly 15-40 words), easy to read aloud.
- image_prompt is in English, describes a single still image for that scene.
- title is a short catchy video title in the narration language.
- description is a 1-2 sentence summary in the narration language.
- Return exactly this JSON shape:
{{"title": "...", "description": "...", "scenes": [{{"narration": "...", "image_prompt": "...", "on_screen_text": "..."}}]}}
- on_screen_text is a punchy 2-5 word caption for that scene, or "" if not needed."""


def generate_storyboard(topic: str, *, language: str = "id", scene_count: int = 6,
                        style: str = "cinematic", tone: str = "informatif dan menarik",
                        extra: str = "") -> dict[str, Any]:
    """Minta LLM gratis membuat storyboard terstruktur.

    Beberapa model dicoba berurutan karena endpoint gratis ini sering
    membatasi laju saat sibuk.
    """
    prompt = _storyboard_user_prompt(
        topic, config.VOICES.get(language, {}).get("label", language),
        scene_count, style, tone, extra)

    models = [config.DEFAULT_TEXT_MODEL, "openai-fast", "mistral"]
    errors: list[str] = []
    seen: set[str] = set()
    for model in models:
        if model in seen:
            continue
        seen.add(model)
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.85,
        }
        try:
            raw = _request(
                config.POLLINATIONS_TEXT_ENDPOINT,
                data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json"},
                timeout=180, retries=2, backoff=3.0,
            )
            board = _parse_storyboard(raw, scene_count)
            if model != config.DEFAULT_TEXT_MODEL:
                log.info("Storyboard dibuat dengan model cadangan '%s'.", model)
            return board
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{model}: {exc}")
            log.warning("Storyboard gagal via '%s': %s", model, exc)
            time.sleep(1.5)

    raise ProviderError("Semua model teks gagal: " + " | ".join(errors[:3]))


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1] if "```" in text[3:] else text[3:]
        text = text.removeprefix("json").strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ProviderError("Model tidak mengembalikan JSON.")
    return json.loads(text[start:end + 1])


def _parse_storyboard(raw_response: bytes, scene_count: int) -> dict[str, Any]:
    envelope = json.loads(raw_response.decode("utf-8", "replace"))
    content = envelope["choices"][0]["message"]["content"]
    if isinstance(content, list):  # sebagian model mengembalikan array parts
        content = "".join(p.get("text", "") for p in content)
    data = _extract_json(content)

    scenes = [s for s in data.get("scenes", []) if str(s.get("narration", "")).strip()]
    if not scenes:
        raise ProviderError("Storyboard tidak berisi scene yang valid.")

    normalised = []
    for s in scenes[:config.MAX_SCENES]:
        normalised.append({
            "narration": str(s.get("narration", "")).strip(),
            "image_prompt": str(s.get("image_prompt", "")).strip() or str(s.get("narration", "")).strip(),
            "on_screen_text": str(s.get("on_screen_text", "") or "").strip(),
        })

    return {
        "title": str(data.get("title") or "Video Tanpa Judul").strip(),
        "description": str(data.get("description") or "").strip(),
        "scenes": normalised,
    }


def refine_image_prompt(prompt: str, style: str) -> str:
    """Tambahkan kata kunci sinematik secara offline (hemat kuota)."""
    if not style or style.lower() in prompt.lower():
        return prompt
    return f"{prompt}, {style}"


# =====================================================================
# 3. TEXT-TO-SPEECH (edge-tts, gratis)
# =====================================================================
@dataclass
class Speech:
    audio_path: Path
    duration: float
    word_timings: list[tuple[float, float, str]] = field(default_factory=list)


async def _synthesize(text: str, voice: str, rate: str, out_path: Path) -> list[tuple[float, float, str]]:
    import edge_tts

    communicate = edge_tts.Communicate(text, voice, rate=rate, boundary="WordBoundary")
    timings: list[tuple[float, float, str]] = []
    with open(out_path, "wb") as fh:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                fh.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                timings.append((
                    chunk["offset"] / 1e7,
                    chunk["duration"] / 1e7,
                    chunk["text"],
                ))
    return timings


def text_to_speech(text: str, *, voice: str = "id-ID-ArdiNeural", rate: str = "+0%",
                   out_path: Path | None = None) -> Speech:
    """Sintesis suara gratis via edge-tts, lengkap dengan timing per kata."""
    from .media import probe_duration

    key = hashlib.sha256(f"{text}|{voice}|{rate}".encode()).hexdigest()[:20]
    audio_path = out_path or (config.CACHE_DIR / f"tts_{key}.mp3")

    if audio_path.exists() and audio_path.stat().st_size > 512:
        timings_path = audio_path.with_suffix(".json")
        timings = json.loads(timings_path.read_text()) if timings_path.exists() else []
        return Speech(audio_path, probe_duration(audio_path), timings)

    timings: list[tuple[float, float, str]] = []
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            timings = asyncio.run(_synthesize(text, voice, rate, audio_path))
            last_err = None
            break
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            log.warning("TTS gagal (%s), percobaan %d/3", exc, attempt + 1)
            time.sleep(1.5 * (attempt + 1))
    if last_err is not None:
        raise ProviderError(f"Text-to-speech gagal: {last_err}") from last_err

    if not audio_path.exists() or audio_path.stat().st_size < 512:
        raise ProviderError("Text-to-speech menghasilkan berkas kosong.")

    audio_path.with_suffix(".json").write_text(json.dumps(timings))
    return Speech(audio_path, probe_duration(audio_path), timings)


def tts_preview(voice: str, sample_text: str, out_path: Path) -> Path:
    speech = text_to_speech(sample_text, voice=voice, out_path=out_path)
    return speech.audio_path
