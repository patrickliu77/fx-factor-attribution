"""Offline Windows System.Speech and FFmpeg adapter; no speaker output or API."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import wave


def render(transcript: Path, destination: Path, lang: str):
    if os.name != "nt" or lang not in {"en", "zh"}:
        raise RuntimeError("windows_speech_unavailable")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("audio_encoder_unavailable")
    if destination.exists():
        raise ValueError("audio_destination_exists")
    script = Path(__file__).resolve().parents[3] / "ops" / "synthesize_briefing.ps1"
    wav = destination.with_suffix(".wav")
    options = dict(capture_output=True, encoding="utf-8", errors="replace",
                   creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        result = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                                 "-File", str(script), "-TextFile", str(transcript),
                                 "-WaveFile", str(wav), "-Language", lang], timeout=180, **options)
        if result.returncode:
            raise RuntimeError("speech_synthesis_failed")
        voice = json.loads(result.stdout.lstrip("\ufeff"))
        with wave.open(str(wav), "rb") as audio:
            duration = audio.getnframes() / audio.getframerate()
        if not 10 <= duration <= 240:
            raise ValueError("audio_duration_out_of_bounds")
        # Bring spoken copy into the requested 1..3 minute range if a local
        # voice is unusually fast or slow. The real duration is checked below.
        tempo = min(1.35, max(0.75, duration / 150)) if duration > 180 or duration < 60 else 1.0
        args = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-n", "-i", str(wav),
                "-map_metadata", "-1", "-vn", "-ac", "1", "-ar", "24000", "-af", f"atempo={tempo}",
                "-codec:a", "libmp3lame", "-b:a", "64k", str(destination)]
        done = subprocess.run(args, timeout=45, **options)
        if done.returncode or not destination.is_file() or destination.stat().st_size < 1000:
            raise RuntimeError("audio_encoding_failed")
        # MP3 encoder delay differs from source WAV duration. Inspect the file
        # readers will actually play, not an estimated word count.
        probe = shutil.which("ffprobe")
        if not probe:
            raise RuntimeError("audio_probe_unavailable")
        measured = subprocess.run([probe, "-v", "error", "-show_entries", "format=duration",
                                   "-of", "json", str(destination)], timeout=20, **options)
        if measured.returncode:
            raise RuntimeError("audio_probe_failed")
        duration = float(json.loads(measured.stdout)["format"]["duration"])
        if not 60 <= duration <= 180:
            raise ValueError("audio_duration_out_of_bounds")
        return {"engine": "windows-system-speech", "voice": voice["voice"],
                "duration_seconds": round(duration, 3), "tempo": tempo}
    finally:
        # Only this invocation's temporary WAV is removed. Final MP3s are owned
        # by the attachment store and are never overwritten by this adapter.
        wav.unlink(missing_ok=True)
