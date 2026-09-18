# 🎬 VideoAI — Generator Video AI 100% Gratis

Buat video lengkap (skrip + gambar + narasi suara + musik + subtitle) hanya dari
sebuah **topik**. Tanpa API key, tanpa login, tanpa langganan, tanpa watermark.

Aplikasi ini berjalan sepenuhnya di komputer Anda — layanan AI yang dipakai
bersifat terbuka dan tidak memerlukan pendaftaran.

---

## Apa yang dihasilkan

Masukkan satu topik, misalnya *"Manfaat bangun pagi untuk kesehatan"*, dan
VideoAI akan:

| Tahap | Hasil |
|---|---|
| 1. Skrip | AI menulis judul, deskripsi, dan storyboard per-scene |
| 2. Gambar | Satu gambar AI sinematik untuk setiap scene |
| 3. Suara | Narasi TTS neural (pria/wanita, 6 bahasa) |
| 4. Musik | Musik latar bebas royalti yang disintesis lokal |
| 5. Gerakan | Efek Ken Burns (zoom/pan) agar gambar terasa hidup |
| 6. Subtitle | Subtitle otomatis dengan sorotan per-kata |
| 7. Render | MP4 siap unggah ke YouTube / Reels / TikTok |

---

## Cara menjalankan

### 1. Pasang FFmpeg (wajib)

```bash
sudo apt-get update && sudo apt-get install -y ffmpeg fonts-dejavu-core
```

### 2. Pasang dependensi Python

```bash
pip install -r requirements.txt
```

### 3. Jalankan aplikasi web

```bash
python3 app.py
```

Buka **http://localhost:12000** di peramban.

### Atau lewat terminal (tanpa peramban)

```bash
# Buat video langsung
python3 -m videoai.app generate "Sejarah singkat Candi Borobudur" -n 6 -a 9:16

# Video vertikal untuk TikTok/Reels, kualitas draft agar cepat
python3 -m videoai.app generate "Resep nasi goreng spesial" -n 4 -a 9:16 -q draft

# Cek kesiapan lingkungan & layanan AI
python3 -m videoai.app check
```

Hasil video tersimpan di folder `output/`.

---

## Mengapa ini benar-benar gratis?

| Komponen | Penyedia | Biaya |
|---|---|---|
| Gambar AI | [Pollinations.ai](https://pollinations.ai) (FLUX / SANA) | gratis, tanpa API key |
| Skrip & storyboard | Pollinations text API (GPT-OSS 20B) | gratis, tanpa API key |
| Narasi suara | Microsoft Edge TTS (`edge-tts`) | gratis, tanpa API key |
| Musik latar | Disintesis prosedural di komputer Anda | gratis, bebas royalti |
| Render video | FFmpeg di komputer Anda | gratis |

Tidak ada permintaan pembayaran, tidak ada kunci rahasia, tidak ada akun.
Catatan: layanan gratis ini bisa membatasi laju permintaan saat sibuk — aplikasi
akan mencoba ulang beberapa kali, mengganti model gambar, dan (kalau masih gagal)
memakai latar gradien lokal agar video tetap jadi.

---

## Fitur antarmuka web

- **Topik + chip ide** — cepat memulai, atau tulis ide sendiri.
- **Lihat skrip dulu** — tinjau dan sunting judul, narasi, dan prompt gambar
  setiap scene sebelum render.
- **Pratinjau suara** — dengarkan contoh suara sebelum membuat video.
- **Progres real-time** via Server-Sent Events, dengan log langkah demi langkah.
- **Riwayat video** — semua hasil render dengan thumbnail dan tautan unduh.
- **Tiga format**: 16:9 (YouTube), 9:16 (Reels/TikTok/Shorts), 1:1 (feed).
- **Tiga tingkat kualitas**: draft (cepat), high (seimbang), ultra (paling tajam).
- **Seed dapat direproduksi** — seed sama + topik sama menghasilkan video sama.

---

## Opsi CLI

```
python3 -m videoai.app generate TOPIK [opsi]

  -n, --scenes N          jumlah scene 1-12 (default 6)
  -l, --language {id,en,ja,es,ar,ms}   bahasa narasi (default id)
  -a, --aspect {16:9,9:16,1:1}         format video
  -q, --quality {draft,high,ultra}     kualitas render
  -m, --music MOOD        suasana musik (calm, warm, hopeful, uplifting,
                          epic, dark, corporate)
      --voice-gender      male | female
      --style             gaya visual (prompt gaya)
      --image-model       flux | sana
      --no-music          tanpa musik latar
      --no-voice          tanpa narasi suara
      --no-subs           tanpa subtitle
      --no-motion         tanpa efek Ken Burns
      --seed N            seed untuk hasil yang dapat direproduksi
```

---

## API HTTP

| Metode | Endpoint | Keterangan |
|---|---|---|
| `GET` | `/` | Antarmuka web |
| `GET` | `/healthz` | Status FFmpeg, versi, jumlah output |
| `GET` | `/api/options` | Semua pilihan (bahasa, format, gaya, …) |
| `POST` | `/api/generate` | Kirim job pembuatan video → `{job_id}` |
| `GET` | `/api/jobs` | Daftar job |
| `GET` | `/api/jobs/<id>` | Status satu job |
| `GET` | `/api/jobs/<id>/events` | Aliran progres (SSE) |
| `POST` | `/api/script` | Buat storyboard saja untuk ditinjau |
| `POST` | `/api/voices/preview` | Contoh suara MP3 |
| `POST` | `/api/preview/image` | Pratinjau satu gambar AI |
| `GET` | `/api/outputs` | Daftar video hasil |
| `GET` | `/outputs/<file>` | Unduh video / thumbnail |

Contoh:

```bash
curl -X POST http://localhost:12000/api/generate \
  -H 'Content-Type: application/json' \
  -d '{"topic":"Keindahan terumbu karang Indonesia","scene_count":5,"aspect":"16:9"}'
```

---

## Struktur proyek

```
videoai/
├── __init__.py       # paket & versi
├── config.py         # preset, suara, konstanta
├── providers.py      # gambar AI, LLM gratis, text-to-speech
├── music.py          # sintesis musik prosedural (numpy)
├── media.py          # pembungkus FFmpeg: Ken Burns, xfade, ASS, audio
├── pipeline.py       # orkestrasi seluruh proses
├── app.py            # Flask + CLI
├── templates/
│   └── index.html    # antarmuka pengguna
└── static/
    ├── style.css     # tema gelap responsif
    └── app.js        # logika front-end (vanilla JS)
app.py                # titik masuk cepat
output/               # video hasil
assets/cache/         # cache gambar, suara, placeholder
assets/music/         # cache musik
```

---

## Catatan teknis

**Ken Burns tetap tajam.** Gambar gratis dari Pollinations dibatasi ±1024 px.
`media.build_scene_clip()` melakukan upscale Lanczos ke ukuran kerja lebih besar
(overscan 1,15–1,40×), sehingga saat `zoompan` memperbesar frame, hasilnya tidak
buram. Gambar juga dinaikkan resolusinya lalu dipertajam (`UnsharpMask`) sebelum
dirender.

**Transisi tidak memotong narasi.** Setiap scene dibuat lebih panjang dari durasi
narasi (`NARRATION_LEAD` + `SCENE_TAIL_PAD`), dan `scene_start_times()`
menghitung waktu mulai sebenarnya pada timeline setelah semua `xfade` memakan
0,55 detik per sambungan. Narasi serta subtitle kemudian ditempel pada offset
yang tepat.

**Musik bebas royalti.** Tidak ada berkas musik yang diunduh. `music.py`
membangkitkan nada dari nol (pad + bass + bell dalam tangga nada tertentu),
lalu FFmpeg mengubahnya menjadi MP3. Hasilnya aman untuk penggunaan komersial.

**Audio ducking.** Saat musik dan narasi aktif, `sidechaincompress` memakai
narasi sebagai sidechain sehingga musik otomatis mengecil ketika ada suara.

**Cache di disk.** Gambar, suara, dan musik di-cache berdasarkan hash
parameter. Render ulang dengan seed sama menjadi jauh lebih cepat.

---

## Pemecahan masalah

| Gejala | Solusi |
|---|---|
| `ffmpeg tidak ditemukan` | `sudo apt-get install -y ffmpeg` |
| Gambar gagal / lambat | Layanan gratis sedang membatasi laju. Aplikasi mencoba ulang otomatis, mengganti model, lalu memakai latar gradien lokal. Coba lagi beberapa saat. |
| Video terasa lambat | Pakai `-q draft` dan kurangi jumlah scene. |
| Suara narasi gagal | Periksa koneksi; `edge-tts` butuh akses ke layanan Microsoft. Video tetap dibuat tanpa narasi. |
| Font subtitle kotak-kotak | `sudo apt-get install -y fonts-dejavu-core` |
| Port 12000 terpakai | `python3 app.py serve --port 8080` |

---

## Lisensi

Kode aplikasi ini bebas dipakai dan dimodifikasi. Karya yang Anda hasilkan
(gambar AI, narasi, video) adalah milik Anda. Musik dibuat secara prosedural
sehingga tidak mengandung materi berhak cipta pihak ketiga.
