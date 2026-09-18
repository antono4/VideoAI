"""Aplikasi web Flask + CLI untuk VideoAI."""
from __future__ import annotations

import argparse
import json
import logging
import os
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from flask import (Flask, Response, jsonify, render_template, request,
                   send_from_directory)

from . import config, media, music, pipeline, providers

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
log = logging.getLogger("videoai.app")

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["JSON_AS_ASCII"] = False


# =====================================================================
# Manajemen job
# =====================================================================
@dataclass
class Job:
    id: str
    request: pipeline.VideoRequest
    status: str = "queued"          # queued | running | done | error
    progress: float = 0.0
    message: str = "Menunggu antrean..."
    created: float = field(default_factory=time.time)
    result: dict[str, Any] | None = None
    error: str | None = None
    logs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "status": self.status, "progress": round(self.progress, 4),
            "message": self.message, "created": self.created,
            "result": self.result, "error": self.error,
            "logs": self.logs[-40:],
            "elapsed": round(time.time() - self.created, 1),
        }


class JobManager:
    """Antrean job sederhana dengan satu worker (render video berat)."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._queue: queue.Queue[str] = queue.Queue()
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()

    def submit(self, req: pipeline.VideoRequest) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], request=req)
        with self._lock:
            self._jobs[job.id] = job
        self._queue.put(job.id)
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def all(self, limit: int = 40) -> list[Job]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda j: j.created, reverse=True)
        return jobs[:limit]

    def _update(self, job_id: str, **fields: Any) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                for k, v in fields.items():
                    setattr(job, k, v)

    def _append_log(self, job_id: str, msg: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job.logs.append(msg)

    def _run(self) -> None:
        while True:
            job_id = self._queue.get()
            job = self.get(job_id)
            if job is None:
                continue
            self._update(job_id, status="running", progress=0.01,
                         message="Memulai...")
            try:
                result = pipeline.generate_video(
                    job.request, job_id=job_id,
                    progress=lambda p, m: self._update(job_id, progress=p, message=m),
                    log_fn=lambda m: self._append_log(job_id, m),
                )
                self._update(job_id, status="done", progress=1.0,
                             message="Video selesai!", result=result.to_dict())
            except Exception as exc:  # noqa: BLE001
                log.exception("Job %s gagal", job_id)
                self._update(job_id, status="error", message="Gagal membuat video.",
                             error=str(exc))
            finally:
                self._queue.task_done()


manager = JobManager()


# =====================================================================
# Routes
# =====================================================================
@app.route("/")
def index():
    return render_template("index.html", options=pipeline.available_options(),
                           version=__import__("videoai").__version__)


@app.get("/api/options")
def api_options():
    return jsonify(pipeline.available_options())


@app.post("/api/generate")
def api_generate():
    data = request.get_json(silent=True) or request.form.to_dict() or {}
    topic = str(data.get("topic", "")).strip()
    if not topic:
        return jsonify({"error": "Topik tidak boleh kosong."}), 400
    if len(topic) > 600:
        return jsonify({"error": "Topik terlalu panjang (maks 600 karakter)."}), 400

    def as_bool(key: str, default: bool = True) -> bool:
        v = data.get(key, default)
        if isinstance(v, bool):
            return v
        return str(v).lower() in {"1", "true", "yes", "on"}

    def as_int(key: str, default: int, lo: int, hi: int) -> int:
        try:
            return max(lo, min(hi, int(data.get(key, default))))
        except (TypeError, ValueError):
            return default

    req = pipeline.VideoRequest(
        topic=topic,
        language=str(data.get("language", config.DEFAULT_LANGUAGE)),
        voice_gender=str(data.get("voice_gender", "male")),
        scene_count=as_int("scene_count", config.DEFAULT_SCENE_COUNT,
                           config.MIN_SCENES, config.MAX_SCENES),
        aspect=str(data.get("aspect", "16:9")),
        quality=str(data.get("quality", "high")),
        visual_style=str(data.get("visual_style", pipeline.VideoRequest.visual_style)),
        tone=str(data.get("tone", "informatif dan menarik")),
        extra_direction=str(data.get("extra_direction", "")),
        image_model=str(data.get("image_model", config.DEFAULT_IMAGE_MODEL)),
        music_mood=str(data.get("music_mood", "calm")),
        enable_music=as_bool("enable_music", True),
        enable_narration=as_bool("enable_narration", True),
        enable_subtitles=as_bool("enable_subtitles", True),
        enable_motion=as_bool("enable_motion", True),
        voice_rate=str(data.get("voice_rate", "+0%")),
        seed=as_int("seed", 0, 0, 10_000_000),
        script=data.get("script") if isinstance(data.get("script"), dict) else None,
        image_prompts=data.get("image_prompts") if isinstance(data.get("image_prompts"), list) else None,
        narration_texts=data.get("narration_texts") if isinstance(data.get("narration_texts"), list) else None,
        title=str(data.get("title", "")),
    )
    if req.language not in config.VOICES:
        req.language = config.DEFAULT_LANGUAGE
    if req.aspect not in config.PRESETS:
        req.aspect = "16:9"
    if req.quality not in config.QUALITY:
        req.quality = "high"
    if req.music_mood not in {m["id"] for m in music.list_moods()}:
        req.music_mood = "calm"

    job = manager.submit(req)
    return jsonify({"job_id": job.id, "status": job.status}), 202


@app.get("/api/jobs")
def api_jobs():
    return jsonify([j.to_dict() for j in manager.all()])


@app.get("/api/jobs/<job_id>")
def api_job(job_id: str):
    job = manager.get(job_id)
    if job is None:
        return jsonify({"error": "Job tidak ditemukan."}), 404
    return jsonify(job.to_dict())


@app.get("/api/jobs/<job_id>/events")
def api_job_events(job_id: str):
    """Server-Sent Events untuk progres real-time."""
    if manager.get(job_id) is None:
        return jsonify({"error": "Job tidak ditemukan."}), 404

    def stream():
        last = None
        while True:
            job = manager.get(job_id)
            if job is None:
                break
            payload = json.dumps(job.to_dict(), ensure_ascii=False)
            if payload != last:
                yield f"data: {payload}\n\n"
                last = payload
            if job.status in {"done", "error"}:
                break
            time.sleep(0.6)

    return Response(stream(), mimetype="text/event-stream", headers={
        "Cache-Control": "no-cache", "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    })


@app.post("/api/script")
def api_script():
    """Buat storyboard saja, untuk ditinjau/diedit sebelum render."""
    data = request.get_json(silent=True) or {}
    topic = str(data.get("topic", "")).strip()
    if not topic:
        return jsonify({"error": "Topik tidak boleh kosong."}), 400
    language = str(data.get("language", config.DEFAULT_LANGUAGE))
    if language not in config.VOICES:
        language = config.DEFAULT_LANGUAGE
    try:
        scene_count = max(config.MIN_SCENES, min(config.MAX_SCENES,
                                                 int(data.get("scene_count", 6))))
    except (TypeError, ValueError):
        scene_count = 6
    try:
        board = providers.generate_storyboard(
            topic, language=language, scene_count=scene_count,
            style=str(data.get("visual_style", pipeline.VideoRequest.visual_style)),
            tone=str(data.get("tone", "informatif dan menarik")),
            extra=str(data.get("extra_direction", "")),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("Storyboard gagal: %s", exc)
        board = pipeline._fallback_storyboard(pipeline.VideoRequest(
            topic=topic, language=language, scene_count=scene_count))
        board["fallback"] = True
        board["warning"] = f"Layanan AI teks sibuk, memakai skrip cadangan. ({exc})"
    return jsonify(board)


@app.post("/api/preview/image")
def api_preview_image():
    """Pratinjau cepat satu gambar AI dari prompt."""
    data = request.get_json(silent=True) or {}
    prompt = str(data.get("prompt", "")).strip()
    if not prompt:
        return jsonify({"error": "Prompt tidak boleh kosong."}), 400
    try:
        w = max(256, min(1024, int(data.get("width", 768))))
        h = max(256, min(1024, int(data.get("height", 432))))
        seed = int(data.get("seed", 0)) or abs(hash(prompt)) % 100000
        model = str(data.get("model", config.DEFAULT_IMAGE_MODEL))
    except (TypeError, ValueError):
        return jsonify({"error": "Parameter tidak valid."}), 400
    try:
        path = providers.generate_image(providers.ImageRequest(
            prompt=prompt, width=w, height=h, seed=seed, model=model))
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 502
    return send_from_directory(config.CACHE_DIR, path.name, max_age=3600)


@app.get("/api/voices")
def api_voices():
    return jsonify(pipeline.available_options()["voices"])


@app.post("/api/voices/preview")
def api_voice_preview():
    data = request.get_json(silent=True) or {}
    language = str(data.get("language", config.DEFAULT_LANGUAGE))
    gender = str(data.get("voice_gender", "male"))
    voice = config.VOICES.get(language, config.VOICES["id"]).get(
        gender, config.VOICES["id"]["male"])
    sample = str(data.get("text", "")).strip() or {
        "id": "Halo, ini contoh suara narasi untuk video Anda.",
        "en": "Hello, this is a sample narration voice for your video.",
        "ja": "こんにちは、これはナレーションのサンプルです。",
        "es": "Hola, esta es una muestra de voz para tu vídeo.",
        "ar": "مرحبا، هذه عينة من صوت الراوي.",
        "ms": "Helo, ini contoh suara narasi untuk video anda.",
    }.get(language, "Hello, this is a sample narration voice.")
    out = config.CACHE_DIR / f"preview_{voice}.mp3"
    try:
        if not out.exists() or out.stat().st_size < 512:
            providers.text_to_speech(sample[:220], voice=voice, out_path=out)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 502
    return send_from_directory(config.CACHE_DIR, out.name, max_age=3600)


@app.get("/api/outputs")
def api_outputs():
    items = []
    for f in sorted(config.OUTPUT_DIR.glob("*.mp4"),
                    key=lambda p: p.stat().st_mtime, reverse=True)[:60]:
        meta_file = f.with_suffix(".json")
        meta = {}
        if meta_file.exists():
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                meta = {}
        items.append({
            "name": f.name,
            "url": f"/outputs/{f.name}",
            "size_bytes": f.stat().st_size,
            "modified": f.stat().st_mtime,
            "duration": meta.get("total_duration"),
            "title": meta.get("title") or f.stem,
            "thumbnail": f"/outputs/{f.stem}.jpg"
                         if f.with_suffix(".jpg").exists() else None,
            "job_id": meta.get("job_id"),
        })
    return jsonify(items)


@app.get("/outputs/<path:filename>")
def serve_output(filename: str):
    return send_from_directory(config.OUTPUT_DIR, filename, max_age=3600)


@app.get("/healthz")
def healthz():
    try:
        media.ensure_ffmpeg()
        ffmpeg_ok, ffmpeg_err = True, None
    except Exception as exc:  # noqa: BLE001
        ffmpeg_ok, ffmpeg_err = False, str(exc)
    return jsonify({
        "status": "ok" if ffmpeg_ok else "degraded",
        "version": __import__("videoai").__version__,
        "ffmpeg": ffmpeg_ok, "ffmpeg_error": ffmpeg_err,
        "outputs": len(list(config.OUTPUT_DIR.glob("*.mp4"))),
        "queue": manager._queue.qsize(),
    }), (200 if ffmpeg_ok else 503)


@app.errorhandler(404)
def not_found(_e):
    if request.path.startswith("/api/"):
        return jsonify({"error": "Endpoint tidak ditemukan."}), 404
    return render_template("index.html", options=pipeline.available_options(),
                           version=__import__("videoai").__version__), 404


# =====================================================================
# CLI
# =====================================================================
def main() -> None:
    parser = argparse.ArgumentParser(
        prog="videoai",
        description="VideoAI — buat video AI dari gambar & suara gratis (100% tanpa API key).")
    sub = parser.add_subparsers(dest="cmd")

    serve = sub.add_parser("serve", help="Jalankan aplikasi web")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=12000)
    serve.add_argument("--debug", action="store_true")

    gen = sub.add_parser("generate", help="Buat video langsung dari terminal")
    gen.add_argument("topic", help="Topik atau ide video")
    gen.add_argument("-n", "--scenes", type=int, default=config.DEFAULT_SCENE_COUNT)
    gen.add_argument("-l", "--language", default=config.DEFAULT_LANGUAGE,
                     choices=sorted(config.VOICES))
    gen.add_argument("-a", "--aspect", default="16:9", choices=sorted(config.PRESETS))
    gen.add_argument("-q", "--quality", default="high", choices=sorted(config.QUALITY))
    gen.add_argument("-m", "--music", default="calm",
                     choices=[m["id"] for m in music.list_moods()])
    gen.add_argument("--voice-gender", default="male", choices=["male", "female"])
    gen.add_argument("--style", default=pipeline.VideoRequest.visual_style)
    gen.add_argument("--image-model", default=config.DEFAULT_IMAGE_MODEL)
    gen.add_argument("--no-music", action="store_true")
    gen.add_argument("--no-voice", action="store_true")
    gen.add_argument("--no-subs", action="store_true")
    gen.add_argument("--no-motion", action="store_true")
    gen.add_argument("--seed", type=int, default=0)

    sub.add_parser("check", help="Periksa kesiapan lingkungan")

    args = parser.parse_args()
    if args.cmd in (None, "serve"):
        host = getattr(args, "host", "0.0.0.0")
        port = getattr(args, "port", int(os.getenv("PORT", 12000)))
        log.info("VideoAI berjalan di http://%s:%s", host, port)
        app.run(host=host, port=port, debug=getattr(args, "debug", False),
                threaded=True, use_reloader=False)
        return

    if args.cmd == "check":
        media.ensure_ffmpeg()
        print("FFmpeg       : OK")
        print(f"Output folder: {config.OUTPUT_DIR}")
        for name, fn in (("Image AI", lambda: providers.generate_image(
                providers.ImageRequest("test", 384, 384, 1))),
                ("Text AI", lambda: providers.generate_storyboard("test", scene_count=1)),
                ("Voice AI", lambda: providers.text_to_speech("tes", voice="id-ID-ArdiNeural"))):
            try:
                fn()
                print(f"{name:<13}: OK")
            except Exception as exc:  # noqa: BLE001
                print(f"{name:<13}: GAGAL -> {exc}")
        return

    req = pipeline.VideoRequest(
        topic=args.topic, language=args.language, voice_gender=args.voice_gender,
        scene_count=args.scenes, aspect=args.aspect, quality=args.quality,
        visual_style=args.style, image_model=args.image_model, music_mood=args.music,
        enable_music=not args.no_music, enable_narration=not args.no_voice,
        enable_subtitles=not args.no_subs, enable_motion=not args.no_motion,
        seed=args.seed,
    )
    bar_width = 34

    def on_progress(pct: float, msg: str) -> None:
        filled = int(bar_width * pct)
        print(f"\r[{'█' * filled}{'░' * (bar_width - filled)}] {pct * 100:5.1f}%  {msg[:64]}",
              end="", flush=True)

    result = pipeline.generate_video(req, progress=on_progress)
    print(f"\n\n✅ Video: {result.video_path}")
    print(f"   Durasi: {result.duration:.1f}s | Ukuran: {result.size_bytes / 1e6:.1f} MB"
          f" | Waktu render: {result.elapsed:.0f}s")


if __name__ == "__main__":
    main()
