"""Pipeline lengkap: topik -> storyboard -> gambar -> suara -> video."""
from __future__ import annotations

import json
import logging
import shutil
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from PIL import Image, ImageEnhance, ImageFilter

from . import config, media, music, providers

log = logging.getLogger("videoai.pipeline")

ProgressFn = Callable[[float, str], None]


@dataclass
class VideoRequest:
    topic: str
    language: str = config.DEFAULT_LANGUAGE
    voice_gender: str = "male"
    scene_count: int = config.DEFAULT_SCENE_COUNT
    aspect: str = "16:9"
    quality: str = "high"
    visual_style: str = "cinematic photography, dramatic natural light, 35mm, high detail"
    tone: str = "informatif dan menarik"
    extra_direction: str = ""
    image_model: str = config.DEFAULT_IMAGE_MODEL
    music_mood: str = "calm"
    enable_music: bool = True
    enable_narration: bool = True
    enable_subtitles: bool = True
    enable_motion: bool = True
    voice_rate: str = "+0%"
    seed: int = 0
    script: dict[str, Any] | None = None      # storyboard siap pakai (opsional)
    image_prompts: list[str] | None = None    # override prompt gambar
    narration_texts: list[str] | None = None  # override narasi
    title: str = ""


@dataclass
class VideoResult:
    job_id: str
    video_path: Path
    thumbnail_path: Path | None
    title: str
    description: str
    duration: float
    size_bytes: int
    scenes: list[dict[str, Any]] = field(default_factory=list)
    elapsed: float = 0.0
    log_lines: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["video_path"] = str(self.video_path)
        data["thumbnail_path"] = str(self.thumbnail_path) if self.thumbnail_path else None
        return data


def _enhance_image(src: Path, dst: Path, *, target: tuple[int, int]) -> Path:
    """Perbaiki kualitas gambar AI: upscale 2x + sharpen + warna.

    Gambar gratis dari Pollinations maksimal ~1024 px, jadi upscale lokal
    diperlukan agar Ken Burns pada 1080p/1920p tetap tajam.
    """
    with Image.open(src) as im:
        im = im.convert("RGB")
        short_side_needed = int(max(target) * 0.72)
        scale = 1.0
        if min(im.size) < short_side_needed:
            scale = min(3.0, short_side_needed / max(1, min(im.size)))
        if scale > 1.02:
            new_size = (int(im.width * scale), int(im.height * scale))
            im = im.resize(new_size, Image.LANCZOS)
        im = im.filter(ImageFilter.UnsharpMask(radius=2.0, percent=115, threshold=3))
        im = ImageEnhance.Color(im).enhance(1.05)
        im = ImageEnhance.Contrast(im).enhance(1.04)
        dst.parent.mkdir(parents=True, exist_ok=True)
        im.save(dst, "JPEG", quality=95, optimize=True, subsampling=0)
    return dst


def _image_request_for(prompt: str, target: tuple[int, int], seed: int,
                       model: str) -> providers.ImageRequest:
    """Minta gambar pada rasio target, dibatasi 1024 px sisi terpanjang."""
    tw, th = target
    landscape = tw >= th
    if landscape:
        w, h = 1024, max(384, int(round(1024 * th / tw / 2) * 2))
    else:
        w, h = max(384, int(round(1024 * tw / th / 2) * 2)), 1024
    return providers.ImageRequest(prompt=prompt, width=w, height=h, seed=seed, model=model)


def generate_video(req: VideoRequest, *, job_id: str | None = None,
                   progress: ProgressFn | None = None,
                   log_fn: Callable[[str], None] | None = None) -> VideoResult:
    """Hasilkan video lengkap dari sebuah topik."""
    job_id = job_id or uuid.uuid4().hex[:12]
    started = time.time()
    lines: list[str] = []

    def emit(pct: float, msg: str) -> None:
        lines.append(f"[{pct * 100:5.1f}%] {msg}")
        log.info("%s (%s)", msg, job_id)
        if progress:
            progress(pct, msg)
        if log_fn:
            log_fn(msg)

    work = config.WORK_DIR / job_id
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)

    media.ensure_ffmpeg()
    target = config.PRESETS.get(req.aspect, config.PRESETS["16:9"])
    q = config.QUALITY.get(req.quality, config.QUALITY["high"])
    seed = req.seed or abs(hash(job_id + req.topic)) % 1_000_000

    # ---------------------------------------------------------- 1. storyboard
    if req.script and req.script.get("scenes"):
        storyboard = req.script
        emit(0.06, "Memakai storyboard yang diberikan.")
    else:
        emit(0.02, f"AI sedang menulis skrip tentang “{req.topic}”...")
        try:
            storyboard = providers.generate_storyboard(
                req.topic, language=req.language, scene_count=req.scene_count,
                style=req.visual_style, tone=req.tone, extra=req.extra_direction)
        except Exception as exc:  # noqa: BLE001
            log.warning("Storyboard AI gagal (%s); memakai skrip cadangan.", exc)
            emit(0.05, "Layanan AI teks sibuk - memakai skrip otomatis cadangan.")
            storyboard = _fallback_storyboard(req)
        emit(0.10, f"Skrip siap: “{storyboard.get('title', req.topic)}” "
                   f"({len(storyboard['scenes'])} scene).")

    scenes = list(storyboard["scenes"])
    if req.image_prompts:
        for i, p in enumerate(req.image_prompts):
            if i < len(scenes) and str(p).strip():
                scenes[i]["image_prompt"] = str(p).strip()
    if req.narration_texts:
        for i, t in enumerate(req.narration_texts):
            if i < len(scenes) and str(t).strip():
                scenes[i]["narration"] = str(t).strip()
    if req.title:
        storyboard["title"] = req.title

    # ---------------------------------------------------------- 2. suara
    voice = config.VOICES.get(req.language, config.VOICES["id"]).get(
        req.voice_gender, config.VOICES["id"]["male"])
    per_scene_speech: list[providers.Speech | None] = []
    if req.enable_narration:
        for i, scene in enumerate(scenes):
            text = scene["narration"]
            pct = 0.10 + 0.22 * (i / max(1, len(scenes)))
            emit(pct, f"Membuat suara narasi scene {i + 1}/{len(scenes)}...")
            try:
                per_scene_speech.append(providers.text_to_speech(
                    text, voice=voice, rate=req.voice_rate))
            except Exception as exc:  # noqa: BLE001
                log.error("TTS scene %d gagal: %s", i + 1, exc)
                emit(pct, f"Scene {i + 1}: suara gagal, memakai durasi perkiraan.")
                per_scene_speech.append(None)
    else:
        per_scene_speech = [None] * len(scenes)

    # ---------------------------------------------------------- 3. gambar
    emit(0.33, "Membuat gambar AI (gratis, tanpa API key)...")
    requests = [
        _image_request_for(
            providers.refine_image_prompt(scene["image_prompt"], req.visual_style),
            target, seed + i * 977, req.image_model)
        for i, scene in enumerate(scenes)
    ]

    def img_progress(msg: str, frac: float) -> None:
        emit(0.33 + 0.27 * frac, msg)

    images = providers.generate_images_batch(
        requests, progress=img_progress, max_workers=2, on_error="fallback")

    if all(p is None for p in images):
        raise providers.ProviderError(
            "Semua pembuatan gambar gagal. Periksa koneksi internet lalu coba lagi.")

    # Ganti gambar yang gagal dengan placeholder lokal (offline, gratis).
    for i, p in enumerate(images):
        if p is None:
            images[i] = _placeholder_image(scenes[i], seed + i, target)
            emit(0.60, f"Scene {i + 1}: gambar AI gagal, memakai latar gradien lokal.")

    enhanced_dir = work / "enhanced"
    enhanced: list[Path] = []
    for i, img in enumerate(images):
        dst = enhanced_dir / f"scene_{i:02d}.jpg"
        with Image.open(img) as probe_im:
            isize = probe_im.size
        if isize[0] < target[0] or isize[1] < target[1]:
            _enhance_image(img, dst, target=target)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(img, dst)
        enhanced.append(dst)

    # ---------------------------------------------------------- 4. klip scene
    emit(0.62, "Merender gerakan kamera tiap scene...")
    clip_durations: list[float] = []
    scene_meta: list[dict[str, Any]] = []
    for i, scene in enumerate(scenes):
        speech = per_scene_speech[i]
        narr_dur = speech.duration if speech else _estimate_duration(scene["narration"])
        # Scene harus lebih panjang dari narasi agar crossfade tidak memotong kata.
        duration = max(3.2, narr_dur + config.NARRATION_LEAD + config.SCENE_TAIL_PAD)
        clip_durations.append(duration)
        scene_meta.append({
            "index": i,
            "narration": scene["narration"],
            "image_prompt": scene["image_prompt"],
            "on_screen_text": scene.get("on_screen_text", ""),
            "duration": round(duration, 3),
            "narration_duration": round(narr_dur, 3),
        })

    motion_on = req.enable_motion
    clips: list[Path] = []
    for i, img in enumerate(enhanced):
        out = work / f"clip_{i:02d}.mp4"
        motion = (media.pick_motion(seed, i) if motion_on else media.Motion(1.0, 1.0))
        with Image.open(img) as im:
            isize = im.size
        media.build_scene_clip(
            img, out, clip_durations[i], motion=motion, target=target,
            crf=q["crf"], preset=q["preset"], image_size=isize, overscan=q["overscan"])
        emit(0.62 + 0.14 * ((i + 1) / len(enhanced)),
             f"Scene {i + 1}/{len(enhanced)} dirender ({clip_durations[i]:.1f}s).")
        clips.append(out)

    # ---------------------------------------------------------- 5. gabung
    transition = min(config.TRANSITION_SECONDS,
                     (min(clip_durations) / 3) if clip_durations else 0.4)
    emit(0.77, "Menggabungkan scene dengan transisi halus...")
    silent = work / "video_silent.mp4"
    media.concat_with_transitions(
        clips, silent, transition=transition, durations=clip_durations,
        crf=q["crf"], preset=q["preset"])

    total = media.total_duration(clip_durations, transition)
    starts = media.scene_start_times(clip_durations, transition)
    for i, meta in enumerate(scene_meta):
        meta["start"] = round(starts[i], 3)

    # ---------------------------------------------------------- 6. narasi
    current = silent
    if req.enable_narration and any(per_scene_speech):
        emit(0.83, "Menyusun trek narasi...")
        track = media.build_narration_track(
            [(s.audio_path, starts[i] + config.NARRATION_LEAD)
             for i, s in enumerate(per_scene_speech) if s is not None],
            work / "narration.m4a", total=total)
        voiced = work / "video_voiced.mp4"
        media.attach_audio(silent, track, voiced, total=total)
        current = voiced

    # ---------------------------------------------------------- 7. musik
    if req.enable_music:
        emit(0.88, f"Menambahkan musik latar (mood: {req.music_mood}, bebas royalti)...")
        try:
            track = music.music_for_mood(req.music_mood, total + 2.0, seed=seed % 97)
            out = work / "video_music.mp4"
            if req.enable_narration and any(per_scene_speech):
                media.add_music(current, track, out, duck=True)
            else:
                media.add_music(current, track, out, duck=False)
            current = out
        except Exception as exc:  # noqa: BLE001
            log.error("Musik gagal: %s", exc)
            emit(0.89, "Musik latar dilewati karena kesalahan teknis.")

    # ---------------------------------------------------------- 8. subtitle
    if req.enable_subtitles:
        emit(0.93, "Menempelkan subtitle per-kata...")
        words: list[tuple[float, float, str]] = []
        for i, speech in enumerate(per_scene_speech):
            if not speech or not speech.word_timings:
                continue
            off = starts[i] + config.NARRATION_LEAD
            words.extend((w[0] + off, w[1], w[2]) for w in speech.word_timings)
        if words:
            words.sort(key=lambda w: w[0])
            ass = media.build_ass(words, work / "subs.ass", target=target, offset=0.0)
            subbed = work / "video_subbed.mp4"
            media.burn_subtitles(current, ass, subbed, crf=q["crf"], preset=q["preset"])
            current = subbed
        else:
            emit(0.94, "Tidak ada timing kata; subtitle dilewati.")

    # ---------------------------------------------------------- 9. finalisasi
    emit(0.97, "Menyelesaikan berkas akhir...")
    safe_name = _slugify(storyboard.get("title") or req.topic)[:60] or "video"
    final = config.OUTPUT_DIR / f"{safe_name}_{job_id}.mp4"
    shutil.copy2(current, final)

    thumb = None
    try:
        thumb = media.make_thumbnail(final, config.OUTPUT_DIR / f"{safe_name}_{job_id}.jpg",
                                     at=min(1.5, total * 0.2))
    except Exception as exc:  # noqa: BLE001
        log.warning("Thumbnail gagal: %s", exc)

    (config.OUTPUT_DIR / f"{safe_name}_{job_id}.json").write_text(
        json.dumps({
            "job_id": job_id, "request": asdict(req) | {"script": None,
                                                        "image_prompts": None,
                                                        "narration_texts": None},
            "title": storyboard.get("title"), "description": storyboard.get("description"),
            "total_duration": round(total, 3), "transition": round(transition, 3),
            "scenes": scene_meta, "voice": voice if req.enable_narration else None,
            "music_mood": req.music_mood if req.enable_music else None,
            "aspect": req.aspect, "quality": req.quality,
        }, ensure_ascii=False, indent=2), encoding="utf-8")

    shutil.rmtree(work, ignore_errors=True)
    emit(1.0, "Selesai!")

    return VideoResult(
        job_id=job_id, video_path=final, thumbnail_path=thumb,
        title=storyboard.get("title") or req.topic,
        description=storyboard.get("description", ""),
        duration=media.probe_duration(final),
        size_bytes=final.stat().st_size, scenes=scene_meta,
        elapsed=round(time.time() - started, 2), log_lines=lines,
    )


# ------------------------------------------------------------------ helpers
def _estimate_duration(text: str) -> float:
    """Perkiraan durasi bicara bila TTS gagal (~2.6 kata/detik)."""
    return max(1.6, len(text.split()) / 2.6)


def _placeholder_image(scene: dict[str, Any], seed: int, target: tuple[int, int]) -> Path:
    """Latar gradien lokal saat layanan gambar tidak tersedia - tetap gratis."""
    import math as _m

    tw, th = target
    w, h = (1280, int(1280 * th / tw)) if tw >= th else (int(1280 * tw / th), 1280)
    img = Image.new("RGB", (w, h))
    px = img.load()
    palettes = [
        ((24, 32, 78), (196, 92, 130)), ((12, 58, 66), (240, 178, 84)),
        ((40, 20, 60), (96, 180, 210)), ((58, 44, 22), (232, 148, 88)),
        ((18, 40, 34), (140, 208, 160)),
    ]
    c1, c2 = palettes[seed % len(palettes)]
    for y in range(h):
        for x in range(0, w, 4):
            t = (x / w) * 0.55 + (y / h) * 0.45
            t += 0.06 * _m.sin((x + y) / 90.0 + seed)
            t = min(1.0, max(0.0, t))
            col = (int(c1[0] + (c2[0] - c1[0]) * t),
                   int(c1[1] + (c2[1] - c1[1]) * t),
                   int(c1[2] + (c2[2] - c1[2]) * t))
            for dx in range(4):
                if x + dx < w:
                    px[x + dx, y] = col
    path = config.CACHE_DIR / f"placeholder_{seed}_{w}x{h}.jpg"
    img.filter(ImageFilter.GaussianBlur(2.5)).save(path, "JPEG", quality=92)
    return path


def _fallback_storyboard(req: VideoRequest) -> dict[str, Any]:
    """Storyboard offline ketika LLM gratis sedang tidak tersedia."""
    n = max(1, min(config.MAX_SCENES, req.scene_count))
    topic = req.topic.strip()
    beats_id = [
        ("pembuka", f"Mari kita bahas {topic}. Topik ini menarik untuk kita pahami bersama."),
        ("latar", f"Untuk memulainya, penting mengetahui latar belakang {topic}."),
        ("inti", f"Bagian terpenting dari {topic} adalah bagaimana hal itu bekerja."),
        ("contoh", f"Ada banyak contoh nyata penerapan {topic} di sekitar kita."),
        ("manfaat", f"Dengan memahami {topic}, kita bisa mengambil manfaat yang lebih besar."),
        ("tantangan", f"Meski begitu, {topic} juga punya tantangan yang perlu diwaspadai."),
        ("masa depan", f"Ke depan, {topic} diprediksi akan terus berkembang pesat."),
        ("penutup", f"Demikian ulasan singkat tentang {topic}. Semoga bermanfaat."),
    ]
    beats_en = [
        ("intro", f"Let's talk about {topic}. It is a topic worth understanding."),
        ("background", f"To begin, it helps to know the background of {topic}."),
        ("core", f"The most important part of {topic} is how it actually works."),
        ("examples", f"There are many real-world examples of {topic} around us."),
        ("benefits", f"Understanding {topic} lets us take far greater advantage of it."),
        ("challenges", f"Still, {topic} comes with challenges we should watch for."),
        ("future", f"Going forward, {topic} is expected to keep evolving quickly."),
        ("closing", f"That's a quick look at {topic}. We hope it was useful."),
    ]
    beats = beats_id if req.language == "id" else beats_en
    scenes = []
    for i in range(n):
        beat, narration = beats[i % len(beats)]
        scenes.append({
            "narration": narration,
            "image_prompt": f"{topic}, {beat} moment, {req.visual_style}",
            "on_screen_text": topic.title()[:28],
        })
    return {
        "title": topic.title()[:70],
        "description": f"Video otomatis tentang {topic}.",
        "scenes": scenes,
    }


def _slugify(text: str) -> str:
    import re
    import unicodedata

    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    return re.sub(r"[-\s]+", "-", text)


def available_options() -> dict[str, Any]:
    return {
        "languages": [
            {"id": k, "label": v["label"]} for k, v in config.VOICES.items()
        ],
        "aspects": [
            {"id": "16:9", "label": "16:9 — YouTube / Landscape", "size": list(config.PRESETS["16:9"])},
            {"id": "9:16", "label": "9:16 — Reels / TikTok / Shorts", "size": list(config.PRESETS["9:16"])},
            {"id": "1:1", "label": "1:1 — Feed Instagram", "size": list(config.PRESETS["1:1"])},
        ],
        "qualities": [
            {"id": "draft", "label": "Draft — paling cepat"},
            {"id": "high", "label": "High — seimbang (disarankan)"},
            {"id": "ultra", "label": "Ultra — paling tajam, lambat"},
        ],
        "image_models": [
            {"id": "flux", "label": "FLUX (kualitas terbaik)"},
            {"id": "sana", "label": "SANA (paling cepat)"},
        ],
        "moods": music.list_moods(),
        "voices": {k: {"male": v["male"], "female": v["female"]}
                   for k, v in config.VOICES.items()},
        "scene_range": [config.MIN_SCENES, config.MAX_SCENES],
        "styles": [
            "cinematic photography, dramatic natural light, 35mm, high detail",
            "anime style, vibrant colors, studio ghibli inspired, detailed linework",
            "3D render, octane render, soft studio lighting, ultra detailed",
            "watercolor painting, soft pastel palette, textured paper",
            "cyberpunk neon, rainy night, volumetric light, moody atmosphere",
            "minimalist flat illustration, bold shapes, clean composition",
            "photorealistic macro photography, shallow depth of field",
            "vintage film look, grainy, warm sepia tones",
        ],
    }
