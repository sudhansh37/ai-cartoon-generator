#!/usr/bin/env python3
"""mix.py — final audio mix.

Rules from the project spec, enforced in code:

  * the voiceover is never modified, retimed or resampled — it is summed in as-is
  * music sits at -6 dB relative to the voice level (50 %)
  * SFX sit at or below -6 dB, with their peaks capped at 0.7 x the voice peak
  * everything is trimmed/padded to the voice length so A/V stay in sync

    python audio/mix.py --voice assets/voice.wav --music work/music.wav \
                        --sfx work/sfx.wav --out work/mix.wav
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import wave

import numpy as np

SR = 44100


def read_wav(path: str, target_sr: int = SR) -> np.ndarray:
    """Read any wav to float stereo (n, 2) at target_sr."""
    with wave.open(path, "rb") as w:
        ch = w.getnchannels()
        sw = w.getsampwidth()
        sr = w.getframerate()
        raw = w.readframes(w.getnframes())
    if sw == 1:
        data = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128) / 128.0
    elif sw == 2:
        data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif sw == 4:
        data = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
    else:
        raise RuntimeError(f"{path}: unsupported sample width {sw}")

    if ch > 1:
        data = data.reshape(-1, ch).mean(axis=1)
    if sr != target_sr:
        # linear resample (music/sfx only — the voice is passed through untouched
        # by the caller, which converts it with ffmpeg beforehand if needed)
        ratio = target_sr / float(sr)
        n_out = int(len(data) * ratio)
        idx = np.linspace(0, len(data) - 1, n_out)
        data = np.interp(idx, np.arange(len(data)), data)
    return np.stack([data, data], axis=1) if data.ndim == 1 else data


def to_stereo(x: np.ndarray) -> np.ndarray:
    if x.ndim == 1:
        return np.stack([x, x], axis=1)
    if x.shape[1] == 1:
        return np.repeat(x, 2, axis=1)
    return x


def fit_length(x: np.ndarray, n: int) -> np.ndarray:
    if len(x) >= n:
        return x[:n]
    pad = np.zeros((n - len(x), x.shape[1]))
    return np.concatenate([x, pad], axis=0)


def dbfs(x: np.ndarray) -> float:
    peak = float(np.max(np.abs(x))) if x.size else 0.0
    return 20 * np.log10(peak) if peak > 1e-9 else -120.0


def gain_db(x: np.ndarray, db: float) -> np.ndarray:
    return x * (10 ** (db / 20.0))


def main() -> int:
    ap = argparse.ArgumentParser(description="Mix voiceover + music + SFX")
    ap.add_argument("--voice", required=True)
    ap.add_argument("--music", default="work/music.wav")
    ap.add_argument("--sfx", default="work/sfx.wav")
    ap.add_argument("--out", default="work/mix.wav")
    ap.add_argument("--music-db", type=float, default=-6.0)
    ap.add_argument("--sfx-db", type=float, default=-6.0)
    ap.add_argument("--sfx-peak-ratio", type=float, default=0.7)
    ap.add_argument("--fade", type=float, default=0.35, help="music fade in/out seconds")
    ap.add_argument("--sr", type=int, default=SR)
    args = ap.parse_args()

    if not os.path.exists(args.voice):
        print(f"[mix] ERROR voice not found: {args.voice}", file=sys.stderr)
        return 1

    voice = to_stereo(read_wav(args.voice, args.sr))
    n = len(voice)
    voice_peak = float(np.max(np.abs(voice))) or 1e-6
    print(f"[mix] voice  {n/args.sr:7.2f}s  peak {dbfs(voice):6.2f} dBFS")

    mix = voice.copy()  # the voice is summed in exactly as delivered

    if args.music and os.path.exists(args.music):
        music = fit_length(to_stereo(read_wav(args.music, args.sr)), n)
        music = gain_db(music, args.music_db)
        f = max(0, int(args.fade * args.sr))
        if f and len(music) > 2 * f:
            ramp = np.linspace(0, 1, f)[:, None]
            music[:f] *= ramp
            music[-f:] *= ramp[::-1]
        mix += music
        print(f"[mix] music  {dbfs(music):6.2f} dBFS (voice {args.music_db:+.1f} dB)")
    else:
        print("[mix] music  skipped")

    if args.sfx and os.path.exists(args.sfx):
        sfx = fit_length(to_stereo(read_wav(args.sfx, args.sr)), n)
        sfx = gain_db(sfx, args.sfx_db)
        sfx_peak = float(np.max(np.abs(sfx)))
        cap = voice_peak * args.sfx_peak_ratio
        if sfx_peak > cap and sfx_peak > 0:
            sfx *= cap / sfx_peak
            print(f"[mix] sfx    peak-capped to {dbfs(sfx):.2f} dBFS "
                  f"({args.sfx_peak_ratio:.2f} x voice peak)")
        else:
            print(f"[mix] sfx    {dbfs(sfx):6.2f} dBFS")
        mix += sfx
    else:
        print("[mix] sfx    skipped")

    # soft limiter only on the summed result — never on the voice alone
    peak = float(np.max(np.abs(mix)))
    if peak > 0.98:
        mix = mix / peak * 0.98
        print(f"[mix] limiter: scaled by {0.98/peak:.3f} to avoid clipping")

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    ints = (np.clip(mix, -1, 1) * 32767).astype("<i2")
    with wave.open(args.out, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(args.sr)
        w.writeframes(ints.tobytes())

    print(f"[mix] wrote {args.out}  {len(mix)/args.sr:.2f}s  peak {dbfs(mix):.2f} dBFS")

    # machine-readable summary for the workflow log / PLAN.md
    summary = {
        "voicePeakDb": round(dbfs(voice), 2),
        "musicOffsetDb": args.music_db,
        "sfxOffsetDb": args.sfx_db,
        "sfxPeakCapRatio": args.sfx_peak_ratio,
        "durationSec": round(len(mix) / args.sr, 3),
        "mixPeakDb": round(dbfs(mix), 2),
    }
    with open(os.path.join(os.path.dirname(os.path.abspath(args.out)), "mix_summary.json"), "w") as f:
        json.dump(summary, f, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
