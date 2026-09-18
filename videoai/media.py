"""Pembungkus FFmpeg: probing, Ken Burns, transisi, caption, mixing audio."""
from __future__ import annotations

import json
import logging
import math
import shutil
import subprocess
import textwrap
from dataclasses import dataclass
from pathlib import Path

from . import config

log = logging.getLogger("videoai.media")


class FFmpegError(RuntimeError):
    pass


def ensure_ffmpeg() -> None:
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            raise FFmpegError(
                f"{tool} tidak ditemukan. Pasang dengan: sudo apt-get install -y ffmpeg")
    config.WORK_DIR.mkdir(parents=True, exist_ok=True)


def run_ffmpeg(args: list[str], *, timeout: int = 1800) -> None:
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args]
    log.debug("ffmpeg %s", " ".join(args[:8]))
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-12:]
        raise FFmpegError("FFmpeg gagal:\n" + "\n".join(tail))


def probe(path: Path) -> dict:
    cmd = ["ffprobe", "-v", "error", "-print_format", "json",
           "-show_format", "-show_streams", str(path)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if proc.returncode != 0:
        raise FFmpegError(f"ffprobe gagal untuk {path.name}: {proc.stderr.strip()[:300]}")
    return json.loads(proc.stdout or "{}")


def probe_duration(path: Path) -> float:
    info = probe(path)
    try:
        return float(info["format"]["duration"])
    except (KeyError, TypeError, ValueError):
        for stream in info.get("streams", []):
            if stream.get("duration"):
                return float(stream["duration"])
    return 0.0


# =====================================================================
# Ukuran & fit
# =====================================================================
def fit_size(image_size: tuple[int, int], target: tuple[int, int]) -> tuple[int, int]:
    """Skala gambar agar menutupi frame target tanpa distorsi."""
    iw, ih = image_size
    tw, th = target
    scale = max(tw / iw, th / ih)
    return max(tw, int(math.ceil(iw * scale / 2) * 2)), max(th, int(math.ceil(ih * scale / 2) * 2))


# =====================================================================
# Gerakan kamera (Ken Burns)
# =====================================================================
@dataclass
class Motion:
    """Satu gerakan kamera. zoom 1.0 = seluruh gambar, >1 = diperbesar."""
    start_zoom: float = 1.0
    end_zoom: float = 1.12
    start_xy: tuple[float, float] = (0.5, 0.5)
    end_xy: tuple[float, float] = (0.5, 0.5)

    def label(self) -> str:
        return (f"z{self.start_zoom:.2f}-{self.end_zoom:.2f}"
                f"_x{self.start_xy[0]:.2f}-{self.end_xy[0]:.2f}")


MOTION_PRESETS: dict[str, Motion] = {
    "zoom_in": Motion(1.0, 1.14, (0.5, 0.5), (0.5, 0.5)),
    "zoom_out": Motion(1.14, 1.0, (0.5, 0.5), (0.5, 0.5)),
    "pan_left": Motion(1.18, 1.18, (0.68, 0.5), (0.32, 0.5)),
    "pan_right": Motion(1.18, 1.18, (0.32, 0.5), (0.68, 0.5)),
    "tilt_up": Motion(1.18, 1.18, (0.5, 0.70), (0.5, 0.34)),
    "tilt_down": Motion(1.18, 1.18, (0.5, 0.34), (0.5, 0.70)),
    "drama_in": Motion(1.05, 1.30, (0.5, 0.52), (0.5, 0.46)),
    "reveal_up": Motion(1.26, 1.06, (0.5, 0.72), (0.5, 0.42)),
}


def pick_motion(seed: int, index: int) -> Motion:
    keys = sorted(MOTION_PRESETS)
    return MOTION_PRESETS[keys[(seed + index * 3) % len(keys)]]


def build_scene_clip(image: Path, output: Path, duration: float, *, motion: Motion,
                     target: tuple[int, int], fps: int = config.FPS, crf: int = 18,
                     preset: str = "veryfast", image_size: tuple[int, int] | None = None,
                     overscan: float = 1.30) -> Path:
    """Render satu gambar menjadi klip bergerak (Ken Burns + zoompan).

    Trik penting: gambar diskalakan LEBIH BESAR dari frame target sebelum
    zoompan, sehingga gerakan kamera tidak kehilangan tajam (blur).
    """
    ensure_ffmpeg()
    if image_size is None:
        from PIL import Image
        with Image.open(image) as im:
            image_size = im.size

    frames = max(2, int(round(duration * fps)))
    tw, th = target

    # Ukuran kerja: target * overscan, dibulatkan ke angka genap.
    ww = int(math.ceil(tw * overscan / 2) * 2)
    wh = int(math.ceil(th * overscan / 2) * 2)

    fit_w, fit_h = fit_size(image_size, (ww, wh))
    # Naikkan resolusi sumber lemah agar zoom tetap tajam (lanczos upscale).
    src_factor = max(1.0, 1.24 / max(motion.start_zoom, motion.end_zoom) if max(motion.start_zoom, motion.end_zoom) > 1 else 1.0)
    fw = int(math.ceil(fit_w * src_factor / 2) * 2)
    fh = int(math.ceil(fit_h * src_factor / 2) * 2)

    z0, z1 = motion.start_zoom, motion.end_zoom
    z_expr = f"if(lte(on\\,{frames - 1})\\,{z0}+({z1}-{z0})*on/{frames - 1}\\,{z1})"

    x0, x1 = motion.start_xy[0], motion.end_xy[0]
    y0, y1 = motion.start_xy[1], motion.end_xy[1]
    x_expr = f"min(max(iw*({x0}+({x1}-{x0})*on/{frames - 1})-iw/zoom/2\\,0)\\,iw-iw/zoom)"
    y_expr = f"min(max(ih*({y0}+({y1}-{y0})*on/{frames - 1})-ih/zoom/2\\,0)\\,ih-ih/zoom)"

    vf = (
        f"scale={fw}:{fh}:force_original_aspect_ratio=increase:flags=lanczos,"
        f"crop={fw}:{fh},"
        f"zoompan=z='{z_expr}':x='{x_expr}':y='{y_expr}':d=1:s={tw}x{th}:fps={fps},"
        f"setsar=1,format=yuv420p"
    )

    run_ffmpeg([
        "-loop", "1", "-framerate", str(fps), "-t", f"{duration:.3f}", "-i", str(image),
        "-vf", vf, "-frames:v", str(frames),
        "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
        "-pix_fmt", "yuv420p", "-video_track_timescale", "90000",
        str(output),
    ])
    return output


# =====================================================================
# Caption (ASS)
# =====================================================================
def _ass_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _ass_escape(text: str) -> str:
    return (text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
            .replace("\n", "\\N"))


def caption_style(target: tuple[int, int], *, font_name: str = "DejaVu Sans",
                  bold: bool = True, font_scale: float = 0.042,
                  margin_scale: float = 0.055, color: str = "&H00FFFFFF",
                  outline: int = 4) -> str:
    """Style ASS berbasis ukuran target (aman untuk 16:9 maupun 9:16)."""
    tw, th = target
    font_size = max(20, int(min(tw, th) * font_scale))
    margin_v = max(24, int(th * margin_scale))
    border_style = 1
    return (
        f"Style: Default,{font_name},{font_size},{color},&H000000FF,&H00000000,"
        f"&H96000000,-1,0,0,0,100,100,0,0,{border_style},3,{outline},1,"
        f"{max(20, int(tw * 0.06))},{max(20, int(tw * 0.06))},{margin_v},1"
    )


def build_ass(words: list[tuple[float, float, str]], output: Path, *, target: tuple[int, int],
              offset: float = 0.0, max_chars_per_line: int = 32,
              highlight: str = "&H0000D7FF") -> Path:
    """Buat subtitle ASS dengan sorotan per-kata ala karaoke (tanpa libass karaoke)."""
    tw, th = target
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {tw}
PlayResY: {th}
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
{caption_style(target)}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    # Kelompokkan kata menjadi baris-baris pendek.
    lines: list[list[tuple[float, float, str]]] = []
    current: list[tuple[float, float, str]] = []
    width = 0
    for w in words:
        token_len = len(w[2]) + 1
        if current and width + token_len > max_chars_per_line:
            lines.append(current)
            current, width = [], 0
        current.append(w)
        width += token_len
    if current:
        lines.append(current)

    events: list[str] = []
    for line in lines:
        for i, (start, dur, _word) in enumerate(line):
            line_start = start + offset
            line_end = line[-1][0] + line[-1][1] + offset + 0.22
            if line_end <= line_start:
                continue
            parts = []
            for j, (w_start, _wd, text) in enumerate(line):
                token = _ass_escape(text) + (" " if j < len(line) - 1 else "")
                if j == i:
                    parts.append(f"{{\\c{highlight}}}{token}{{\\c}}")
                elif j < i:
                    parts.append(f"{{\\alpha&H60&}}{token}{{\\alpha&H00&}}")
                else:
                    parts.append(token)
            events.append(
                f"Dialogue: 0,{_ass_time(line_start)},{_ass_time(line_end)},Default,,0,0,0,,"
                + "".join(parts))
    output.write_text(header + "\n".join(events) + "\n", encoding="utf-8")
    return output


def wrap_caption(text: str, max_chars: int = 34) -> str:
    return "\n".join(textwrap.wrap(text, max_chars)) or text


# =====================================================================
# Burn caption & mix audio
# =====================================================================
def burn_subtitles(video: Path, ass_file: Path, output: Path, *, crf: int = 18,
                   preset: str = "veryfast") -> Path:
    ensure_ffmpeg()
    style_path = _escape_filter_path(ass_file)
    vf = f"subtitles='{style_path}':fontsdir=/usr/share/fonts"
    args = ["-i", str(video), "-vf", vf,
            "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
            "-pix_fmt", "yuv420p"]
    if _has_audio(video):
        args += ["-c:a", "copy"]
    args.append(str(output))
    run_ffmpeg(args)
    return output


def _escape_filter_path(path: Path) -> str:
    """Escape path agar aman dipakai di dalam filtergraph FFmpeg."""
    return (path.resolve().as_posix()
            .replace("\\", "\\\\").replace(":", r"\:").replace("'", r"\'"))


def _has_audio(path: Path) -> bool:
    return any(s.get("codec_type") == "audio" for s in probe(path).get("streams", []))


def scene_start_times(durations: list[float], transition: float) -> list[float]:
    """Waktu mulai tiap scene pada timeline akhir (setelah crossfade).

    Klip ke-i mulai pada sum(d_j untuk j<i) - i*transition, karena setiap
    transisi memakan `transition` detik dari durasi total.
    """
    starts, acc = [], 0.0
    for i, d in enumerate(durations):
        starts.append(max(0.0, acc - i * transition))
        acc += d
    return starts


def total_duration(durations: list[float], transition: float) -> float:
    if not durations:
        return 0.0
    return max(0.1, sum(durations) - max(0, len(durations) - 1) * transition)


def concat_with_transitions(clips: list[Path], output: Path, *, transition: float = 0.55,
                            durations: list[float] | None = None,
                            crf: int = 18, preset: str = "veryfast") -> Path:
    """Gabungkan klip video dengan crossfade xfade (video saja)."""
    ensure_ffmpeg()
    if not clips:
        raise FFmpegError("Tidak ada klip untuk digabungkan.")
    if len(clips) == 1:
        run_ffmpeg(["-i", str(clips[0]), "-c", "copy", str(output)])
        return output

    if durations is None:
        durations = [probe_duration(c) for c in clips]
    transition = min(transition, min(durations) / 2.2)

    args: list[str] = []
    for c in clips:
        args += ["-i", str(c)]

    filters: list[str] = []
    v_prev = "0:v"
    offset = durations[0] - transition
    for i in range(1, len(clips)):
        v_out = f"vx{i}"
        filters.append(
            f"[{v_prev}][{i}:v]xfade=transition=fade:duration={transition:.3f}"
            f":offset={max(0.0, offset):.3f}[{v_out}]")
        v_prev = v_out
        offset += durations[i] - transition

    args += [
        "-filter_complex", ";".join(filters),
        "-map", f"[{v_prev}]",
        "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
        "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(output),
    ]
    run_ffmpeg(args)
    return output


def build_narration_track(scene_audio: list[tuple[Path, float]], output: Path,
                          *, total: float) -> Path:
    """Susun semua potongan narasi pada offset waktunya masing-masing.

    scene_audio berisi pasangan (berkas audio, offset_mulai_detik).
    """
    ensure_ffmpeg()
    items = [(p, o) for p, o in scene_audio if p is not None and Path(p).exists()]
    if not items:
        raise FFmpegError("Tidak ada audio narasi.")

    args: list[str] = []
    chains: list[str] = []
    for i, (path, offset) in enumerate(items):
        args += ["-i", str(path)]
        delay_ms = int(max(0.0, offset) * 1000)
        chains.append(
            f"[{i}:a]aresample=48000,adelay={delay_ms}|{delay_ms},"
            f"aformat=channel_layouts=stereo[n{i}]")

    mix_inputs = "".join(f"[n{i}]" for i in range(len(items)))
    chains.append(f"{mix_inputs}amix=inputs={len(items)}:duration=longest:normalize=0[mixed]")
    chains.append(f"[mixed]apad,atrim=0:{max(0.1, total):.3f},"
                  f"asetpts=PTS-STARTPTS,loudnorm=I=-16:TP=-1.5:LRA=11[aout]")

    args += [
        "-filter_complex", ";".join(chains),
        "-map", "[aout]", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-t", f"{max(0.1, total):.3f}", str(output),
    ]
    run_ffmpeg(args)
    return output


def attach_audio(video: Path, audio: Path, output: Path, *, total: float) -> Path:
    """Tempelkan trek audio ke video (menggantikan audio lama bila ada)."""
    ensure_ffmpeg()
    run_ffmpeg([
        "-i", str(video), "-i", str(audio),
        "-map", "0:v", "-map", "1:a",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-t", f"{max(0.1, total):.3f}", "-movflags", "+faststart", str(output),
    ])
    return output


def add_music(video: Path, music: Path, output: Path, *,
              music_volume: float = config.MUSIC_VOLUME, duck: bool = True,
              fade_out: float = 1.5) -> Path:
    """Tambahkan musik latar ke video yang sudah punya narasi.

    Ducking memakai audio narasi sebagai sidechain, sehingga musik otomatis
    mengecil saat ada suara.
    """
    ensure_ffmpeg()
    duration = probe_duration(video)
    fade_start = max(0.0, duration - fade_out)

    if duck:
        chain = (
            f"[0:a]asplit=2[narr][sc];"
            f"[1:a]volume={music_volume},atrim=0:{duration:.3f},asetpts=PTS-STARTPTS[mus];"
            f"[mus][sc]sidechaincompress=threshold=0.025:ratio=9"
            f":attack=10:release=300:makeup=1[duck];"
            f"[narr][duck]amix=inputs=2:duration=first:normalize=0[amixed]"
        )
    else:
        chain = (
            f"[1:a]volume={music_volume},atrim=0:{duration:.3f},asetpts=PTS-STARTPTS[mus];"
            f"[0:a][mus]amix=inputs=2:duration=first:normalize=0[amixed]"
        )

    run_ffmpeg([
        "-i", str(video), "-stream_loop", "-1", "-i", str(music),
        "-filter_complex",
        chain + f";[amixed]afade=t=out:st={fade_start:.3f}:d={fade_out:.3f}[aout]",
        "-map", "0:v", "-map", "[aout]",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-t", f"{duration:.3f}", "-movflags", "+faststart", str(output),
    ])
    return output


def make_thumbnail(video: Path, output: Path, at: float = 1.0) -> Path:
    run_ffmpeg(["-ss", f"{max(0.0, at):.2f}", "-i", str(video), "-frames:v", "1",
                "-q:v", "2", str(output)])
    return output
