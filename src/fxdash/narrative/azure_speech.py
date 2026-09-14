"""Opt-in Azure neural speech. Credentials stay in the local environment.

One bounded request per render; no redirect, automatic retry or voice fallback.
The CLI creates audition files only, never enables tasks or publishes them.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
from xml.sax.saxutils import escape

import requests

VOICES = {
    "en": ("en-US", "en-US-GuyNeural", "newscast"),
    "zh": ("zh-CN", "zh-CN-YunyangNeural", "narration-professional"),
}
MAX_TEXT_CHARS = 4000
MAX_AUDIO_BYTES = 5_000_000


def credentials():
    key = os.environ.get("AZURE_SPEECH_KEY", "").strip()
    region = os.environ.get("AZURE_SPEECH_REGION", "").strip().lower()
    if not key or not region:
        raise RuntimeError("azure_speech_not_configured")
    # No user-supplied host/path: credentials can only go to Microsoft's
    # regional HTTPS service, with redirects disabled below.
    if not re.fullmatch(r"[a-z][a-z0-9]{2,39}", region):
        raise ValueError("invalid_azure_speech_region")
    if (len(key) > 256 or not key.strip('*')
            or any(not 33 <= ord(c) <= 126 for c in key)):
        raise ValueError("invalid_azure_speech_key")
    return key, region


def ssml(text, lang):
    if lang not in VOICES:
        raise ValueError("unsupported_audio_language")
    if not text.strip() or len(text) > MAX_TEXT_CHARS:
        raise ValueError("speech_text_out_of_bounds")
    if any(ord(c) < 32 and c not in "\n\r\t" for c in text):
        raise ValueError("invalid_speech_text")
    locale, voice, style = VOICES[lang]
    # Literal text is escaped, including saved news quotations. It can never
    # insert SSML, external audio, a different voice or a speaking instruction.
    paragraphs = [escape(p.strip()) for p in text.split("\n\n") if p.strip()]
    # audio-v3: 0.95 (old rate) * 1.4 = 1.33 of the voice's default speed.
    # Shorten explicit pauses proportionally; provider timing is approximate.
    body = '<break time="321ms"/>'.join(f"<p>{p}</p>" for p in paragraphs)
    return (f'<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" '
            f'xmlns:mstts="https://www.w3.org/2001/mstts" xml:lang="{locale}">'
            f'<voice name="{voice}"><mstts:express-as style="{style}">'
            f'<prosody rate="+33%">{body}</prosody></mstts:express-as></voice></speak>')


def render(transcript: Path, destination: Path, lang: str, *, preview=False):
    key, region = credentials()
    if destination.exists():
        raise ValueError("audio_destination_exists")
    probe = shutil.which("ffprobe")
    if not probe:
        raise RuntimeError("audio_probe_unavailable")
    text = transcript.read_text(encoding="utf-8")
    body = ssml(text, lang).encode("utf-8")
    started = time.monotonic()
    owned = False
    try:
        # The service returns MP3 directly. Do not stretch or speed up a neural
        # recording to meet the duration contract; reject it for review instead.
        with requests.post(
            f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1",
            headers={"Ocp-Apim-Subscription-Key": key, "Content-Type": "application/ssml+xml",
                     "X-Microsoft-OutputFormat": "audio-24khz-96kbitrate-mono-mp3",
                     "User-Agent": "fx-factor-attribution"},
            data=body, timeout=(10, 60), allow_redirects=False, stream=True,
        ) as response:
            if response.status_code != 200:
                # Never include the response body, request headers or key.
                raise RuntimeError("azure_speech_request_failed")
            if "audio/" not in response.headers.get("Content-Type", "").lower():
                raise RuntimeError("azure_speech_invalid_response")
            size = 0
            with destination.open("xb") as stream:
                owned = True
                for chunk in response.iter_content(chunk_size=65536):
                    size += len(chunk)
                    if size > MAX_AUDIO_BYTES or time.monotonic() - started > 180:
                        raise ValueError("azure_speech_response_out_of_bounds")
                    stream.write(chunk)
            if size < 1000:
                raise ValueError("azure_speech_response_too_short")
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        measured = subprocess.run(
            [probe, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(destination)],
            capture_output=True, encoding="utf-8", timeout=20, **options,
        )
        if measured.returncode:
            raise RuntimeError("audio_probe_failed")
        duration = float(json.loads(measured.stdout)["format"]["duration"])
        lower, upper = (10, 60) if preview else (60, 180)
        if not lower <= duration <= upper:
            raise ValueError("audio_duration_out_of_bounds")
        return {"engine": "azure-neural-speech", "voice": VOICES[lang][1],
                "duration_seconds": round(duration, 3)}
    except Exception:
        if owned:
            destination.unlink(missing_ok=True)
        # Suppress nested provider exceptions, which can contain request details.
        raise RuntimeError("azure_speech_render_failed") from None


SAMPLES = {
    "en": "This is a voice preview, with illustrative figures only. In this example, the dollar's return "
          "against the Japanese yen was plus 12.4 basis points. These figures are provisional.\n\n"
          "The oil contribution was minus 3.2 basis points. Part of the move remains unexplained. "
          "Factor contributions describe associations within the model; they do not establish a cause. "
          "The full briefing includes the data date and source links.",
    "zh": "这是一段音色试听，数字仅为示例。在这个例子中，美元兑日元的收益为正12.4个基点，数字仍待确认。\n\n"
          "布伦特原油的模型贡献为负3.2个基点，仍有一部分波动未被解释。阅读新闻时，可以继续核对事件日期和独立来源。"
          "因子贡献反映模型中的关联，无法证明因果关系。正式简报会附上数据日期与来源链接。",
}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true", help="local configuration check; no network request")
    action.add_argument("--preview", type=Path, metavar="OUTPUT_DIR", help="explicitly request two audition recordings")
    args = parser.parse_args(argv)
    try:
        credentials()
        if not shutil.which("ffprobe"):
            raise RuntimeError("audio_probe_unavailable")
    except (ValueError, RuntimeError) as exc:
        print(json.dumps({"state": str(exc), "network_called": False}))
        return 1
    if args.check:
        print(json.dumps({"state": "configured", "network_called": False,
                          "note": "Account access, quota and voice quality are not verified."}))
        return 0
    args.preview.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="speech-preview-", dir=args.preview) as work:
        result = {}
        for lang, text in SAMPLES.items():
            script = Path(work) / (lang + ".txt")
            script.write_text(text, encoding="utf-8")
            try:
                info = render(script, args.preview / (lang + ".mp3"), lang, preview=True)
                result[lang] = {"state": "ready", **info}
            except Exception:
                result[lang] = {"state": "failed"}
        print(json.dumps(result))
        return 0 if all(v["state"] == "ready" for v in result.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
