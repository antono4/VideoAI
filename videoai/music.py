"""Generator musik latar bebas royalti, dibuat secara prosedural (offline).

Tidak mengunduh materi berhak cipta; nada dibangkitkan dari nol lalu
dirender ke WAV/MP3 oleh FFmpeg. Hasilnya 100% aman dipakai & gratis.
"""
from __future__ import annotations

import logging
import math
import random
import wave
from dataclasses import dataclass, field
from pathlib import Path

from . import config

log = logging.getLogger("videoai.music")

SAMPLE_RATE = 44100

# Tangga nada (semitone relatif terhadap C) dengan nuansa yang berbeda.
SCALES = {
    "calm":      [0, 2, 4, 7, 9],
    "warm":      [0, 2, 4, 5, 7, 9, 11],
    "hopeful":   [0, 2, 4, 7, 9, 11],
    "uplifting": [0, 2, 4, 5, 7, 9, 11],
    "dark":      [0, 2, 3, 5, 7, 8, 10],
    "epic":      [0, 2, 3, 5, 7, 10],
    "corporate": [0, 2, 4, 7, 9, 11],
}


@dataclass
class MusicSpec:
    mood: str = "calm"
    tempo: int = 76
    root: int = 60            # MIDI note C4
    duration: float = 30.0
    brightness: float = 0.5
    seed: int = 0
    layers: list[str] = field(default_factory=lambda: ["pad", "bass", "bell"])


def _midi_to_hz(note: int) -> float:
    return 440.0 * (2.0 ** ((note - 69) / 12.0))


def _envelope(t: float, attack: float, decay: float, sustain: float, release: float,
              length: float) -> float:
    """Envelope ADSR skalar (dipakai untuk perhitungan manual/uji)."""
    if t < 0 or t > length:
        return 0.0
    if t < attack:
        return t / max(1e-6, attack)
    if t < attack + decay:
        k = (t - attack) / max(1e-6, decay)
        return 1.0 + k * (sustain - 1.0)
    if t < length - release:
        return sustain
    return sustain * max(0.0, (length - t) / max(1e-6, release))


def _add_tone(buf, start: float, length: float, freq: float, amp: float,
              *, attack=0.05, decay=0.2, sustain=0.6, release=0.6,
              harmonics: tuple[float, ...] = (1.0, 0.35, 0.12), detune: float = 0.0,
              vibrato: float = 0.0) -> None:
    """Tambahkan satu nada bersintesis ke buffer numpy (vektor, cepat)."""
    import numpy as np

    i0 = max(0, int(start * SAMPLE_RATE))
    i1 = min(len(buf), int((start + length) * SAMPLE_RATE))
    if i1 <= i0:
        return

    t = np.arange(i1 - i0, dtype=np.float64) / SAMPLE_RATE
    # Envelope ADSR potongan-linier.
    env = np.empty_like(t)
    env[:] = sustain
    if attack > 0:
        seg = t < attack
        env[seg] = t[seg] / attack
    a_d_end = attack + decay
    if decay > 0:
        seg = (t >= attack) & (t < a_d_end)
        env[seg] = 1.0 + (t[seg] - attack) / decay * (sustain - 1.0)
    rel_start = length - release
    if release > 0:
        seg = t >= rel_start
        env[seg] = sustain * np.clip((length - t[seg]) / release, 0.0, 1.0)
    env = np.clip(env, 0.0, None)

    vib = 1.0 + vibrato * np.sin(2.0 * math.pi * 5.2 * t) if vibrato else 1.0
    sample = np.zeros_like(t)
    for h_index, h_amp in enumerate(harmonics, start=1):
        f = freq * h_index
        if f > SAMPLE_RATE * 0.45:
            break
        sample += h_amp * np.sin(2.0 * math.pi * f * vib * t)
    if detune:
        # Unison tipis: kesan pad lebar tanpa chorus eksternal.
        sample += 0.55 * np.sin(2.0 * math.pi * freq * (1.0 + detune) * t)

    buf[i0:i1] += amp * env * sample


def make_music(spec: MusicSpec, output: Path) -> Path:
    """Bangkitkan musik ke berkas WAV lalu konversi ke MP3."""
    import numpy as np
    from .media import ensure_ffmpeg, run_ffmpeg

    ensure_ffmpeg()
    rng = random.Random(spec.seed or 1234)
    scale = SCALES.get(spec.mood, SCALES["calm"])

    total = max(4.0, spec.duration)
    n = int(total * SAMPLE_RATE)
    buf = np.zeros(n, dtype=np.float64)

    beat = 60.0 / max(40, spec.tempo)
    bar = beat * 4

    root = spec.root
    degrees = [root + s for s in scale]
    degrees += [root + 12 + s for s in scale]

    # ---- layer pad: akor panjang yang mengambang
    if "pad" in spec.layers:
        chord_steps = [0, 3, 4]
        t = 0.0
        while t < total:
            deg = chord_steps[int(t / (bar * 2)) % len(chord_steps)]
            base = degrees[deg % len(degrees)]
            chord = [base, degrees[(deg + 2) % len(degrees)] + 12,
                     degrees[(deg + 4) % len(degrees)] + 12]
            for note in chord:
                _add_tone(buf, t, bar * 2 + 0.6, _midi_to_hz(note), 0.055,
                          attack=1.4, decay=1.0, sustain=0.72, release=1.6,
                          harmonics=(1.0, 0.22, 0.08), detune=0.0035)
            t += bar * 2

    # ---- layer bass: nada rendah menahan ritme
    if "bass" in spec.layers:
        t = 0.0
        while t < total:
            note = degrees[rng.randrange(min(4, len(degrees)))] - 24
            _add_tone(buf, t, beat * 1.8, _midi_to_hz(note), 0.10,
                      attack=0.02, decay=0.5, sustain=0.4, release=0.7,
                      harmonics=(1.0, 0.15))
            t += beat * 2

    # ---- layer bell: motif melodi acak yang menenangkan
    if "bell" in spec.layers:
        t = beat
        prev = rng.randrange(len(degrees))
        while t < total - 0.5:
            prev = max(0, min(len(degrees) - 1, prev + rng.choice([-2, -1, 0, 1, 2, 3])))
            _add_tone(buf, t, beat * 1.1, _midi_to_hz(degrees[prev] + 12),
                      0.038 * (0.5 + spec.brightness),
                      attack=0.01, decay=0.6, sustain=0.12, release=0.9,
                      harmonics=(1.0, 0.5, 0.24, 0.10), vibrato=0.004)
            t += beat * rng.choice([1, 1, 2])

    # ---- normalisasi lembut + fade agar loop mulus
    peak = float(np.max(np.abs(buf))) if n else 1.0
    buf *= 0.82 / (peak or 1.0)
    fade_in = int(1.2 * SAMPLE_RATE)
    fade_out = int(2.0 * SAMPLE_RATE)
    if fade_in:
        buf[:fade_in] *= np.linspace(0.0, 1.0, fade_in)
    if fade_out and n > fade_out:
        buf[n - fade_out:] *= np.linspace(1.0, 0.0, fade_out)
    pcm = np.clip(buf, -1.0, 1.0)

    raw_wav = output.with_suffix(".gen.wav")
    with wave.open(str(raw_wav), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        stereo = np.repeat((pcm * 32000).astype("<i2")[:, None], 2, axis=1)
        wf.writeframes(stereo.tobytes())

    output.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(["-i", str(raw_wav), "-c:a", "libmp3lame", "-b:a", "160k",
                "-ar", "44100", str(output)])
    raw_wav.unlink(missing_ok=True)
    return output


def music_for_mood(mood: str, duration: float, seed: int = 0) -> Path:
    """Ambil (atau buat) trek musik untuk mood tertentu."""
    key = f"{mood}_{int(duration)}_{seed}"
    target = config.MUSIC_DIR / f"music_{key}.mp3"
    if target.exists() and target.stat().st_size > 4096:
        return target

    presets = {
        "calm":      MusicSpec(mood="calm", tempo=68, root=57, brightness=0.35),
        "warm":      MusicSpec(mood="warm", tempo=72, root=55, brightness=0.45),
        "hopeful":   MusicSpec(mood="hopeful", tempo=78, root=60, brightness=0.6),
        "uplifting": MusicSpec(mood="uplifting", tempo=96, root=62, brightness=0.75),
        "epic":      MusicSpec(mood="epic", tempo=84, root=50, brightness=0.8,
                               layers=["pad", "bass", "bell"]),
        "dark":      MusicSpec(mood="dark", tempo=60, root=48, brightness=0.25,
                               layers=["pad", "bass"]),
        "corporate": MusicSpec(mood="corporate", tempo=100, root=60, brightness=0.65),
    }
    spec = presets.get(mood, presets["calm"])
    spec.duration = max(8.0, duration)
    spec.seed = seed
    return make_music(spec, target)


def list_moods() -> list[dict[str, str]]:
    return [
        {"id": "calm", "label": "Tenang / Calm"},
        {"id": "warm", "label": "Hangat / Warm"},
        {"id": "hopeful", "label": "Penuh Harapan / Hopeful"},
        {"id": "uplifting", "label": "Membangkitkan / Uplifting"},
        {"id": "epic", "label": "Epik / Epic"},
        {"id": "dark", "label": "Gelap / Dark"},
        {"id": "corporate", "label": "Korporat / Corporate"},
    ]
