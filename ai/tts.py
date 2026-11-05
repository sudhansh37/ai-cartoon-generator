#!/usr/bin/env python3
"""tts.py — optional voiceover generation with Gemini TTS.

Only used when the user starts from a script and has no recording. If TTS is not
available on the account, this fails loudly and the workflow tells the user to
upload an MP3/WAV instead — the pipeline never silently ships a silent video.

    python ai/tts.py --script work/script.txt --out assets/voice.wav
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import wave

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gemini import API_ROOT, GeminiClient, GeminiError  # noqa: E402

try:
    import requests
except Exception:  # pragma: no cover
    requests = None  # type: ignore

DEFAULT_TTS_MODELS = [
    os.environ.get("TTS_MODEL") or "gemini-3.8-flash-lite-tts",
    "gemini-2.5-flash-preview-tts",
    "gemini-2.5-pro-preview-tts",
]
VOICES = ["Kore", "Puck", "Charon", "Fenrir", "Aoede", "Leda", "Orus", "Zephyr"]


def synthesize(text: str, out_path: str, voice: str = "Kore", model: str | None = None,
               style: str = "Speak warmly, like a storyteller, unhurried, cinematic.") -> str:
    client = GeminiClient()
    if not client.available:
        raise GeminiError("GEMINI_API_KEY is not set — cannot generate a voiceover")

    models = [m for m in ([model] if model else []) + DEFAULT_TTS_MODELS if m]
    prompt = f"{style}\n\nNarration:\n{text.strip()}"
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}},
            },
        },
    }

    last_error = "unknown error"
    for m in models:
        try:
            resp = requests.post(
                f"{API_ROOT}/{m}:generateContent",
                params={"key": client.api_key},
                json=body,
                timeout=180,
                headers={"Content-Type": "application/json"},
            )
        except Exception as exc:
            last_error = str(exc)
            continue

        if resp.status_code in (401, 403):
            raise GeminiError(f"Gemini authentication failed ({resp.status_code})")
        if resp.status_code == 404:
            last_error = f"model {m} not available"
            continue
        if resp.status_code != 200:
            try:
                last_error = str(resp.json().get("error", {}).get("message", resp.text))[:300]
            except Exception:
                last_error = resp.text[:300]
            continue

        data = resp.json()
        try:
            parts = data["candidates"][0]["content"]["parts"]
        except Exception:
            last_error = "no audio in response"
            continue

        pcm = None
        mime = ""
        for part in parts:
            inline = part.get("inlineData") or part.get("inline_data")
            if inline and inline.get("data"):
                pcm = base64.b64decode(inline["data"])
                mime = inline.get("mimeType") or inline.get("mime_type") or ""
                break
        if not pcm:
            last_error = "response contained no inline audio data"
            continue

        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)

        # Newer TTS models return a complete WAV container; older ones return
        # raw PCM (usually 24 kHz, 16-bit, mono). Handle both.
        if "wav" in (mime or "").lower() or pcm[:4] == b"RIFF":
            with open(out_path, "wb") as f:
                f.write(pcm)
            return out_path

        sr = 24000
        for token in (mime or "").split(";"):
            token = token.strip()
            if token.startswith("rate="):
                try:
                    sr = int(token.split("=")[1])
                except Exception:
                    pass

        with wave.open(out_path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes(pcm)
        return out_path

    raise GeminiError(f"TTS failed: {last_error}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate a voiceover with Gemini TTS (optional)")
    ap.add_argument("--script", default="work/script.txt")
    ap.add_argument("--out", default="assets/voice.wav")
    ap.add_argument("--voice", default=(os.environ.get("TTS_VOICE") or "Kore"), choices=VOICES)
    ap.add_argument("--model", default=os.environ.get("TTS_MODEL") or None)
    ap.add_argument("--style", default="Speak warmly, like a storyteller, unhurried, cinematic.")
    args = ap.parse_args()

    text = ""
    if os.path.exists(args.script):
        with open(args.script, "r", encoding="utf-8") as f:
            text = f.read().strip()
    if not text:
        print(f"[tts] ERROR no script text at {args.script}", file=sys.stderr)
        return 1

    try:
        path = synthesize(text, args.out, args.voice, args.model, args.style)
    except GeminiError as exc:
        print(f"[tts] ERROR {exc}", file=sys.stderr)
        print("[tts] Upload an MP3/WAV voiceover and run the workflow in "
              "'voiceover ready' mode instead.", file=sys.stderr)
        return 1

    size = os.path.getsize(path)
    print(f"[tts] wrote {path} ({size/1024:.0f} KB, voice={args.voice})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
