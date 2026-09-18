#!/usr/bin/env bash
# Pasang semua dependensi VideoAI (FFmpeg + paket Python).
set -e

echo "==> Memasang FFmpeg dan font (butuh sudo)"
if command -v apt-get >/dev/null 2>&1; then
  sudo apt-get update -qq
  sudo apt-get install -y ffmpeg fonts-dejavu-core
elif command -v dnf >/dev/null 2>&1; then
  sudo dnf install -y ffmpeg dejavu-sans-fonts
elif command -v brew >/dev/null 2>&1; then
  brew install ffmpeg
else
  echo "!! Tidak bisa memasang FFmpeg otomatis. Pasang manual lalu jalankan ulang." >&2
fi

echo "==> Memasang paket Python"
python3 -m pip install --upgrade pip >/dev/null
python3 -m pip install -r "$(dirname "$0")/requirements.txt"

echo "==> Memeriksa kesiapan"
python3 -m videoai.app check

echo
echo "Selesai. Jalankan aplikasi dengan:  python3 app.py"