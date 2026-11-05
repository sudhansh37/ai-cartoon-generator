#!/usr/bin/env python3
"""audio.py — procedural music and sound effects (no samples, no downloads).

Everything is synthesised with NumPy: a march-like music loop (kick, snare,
plucked bass, brass pad, optional low drone) and the SFX set the project uses
(whoosh, thunk, crash, rumble, scrape, ting, creak, splash, ambience).

    python audio/audio.py --plan work/plan.json --out-dir work --mood march

Writes:
    work/music.wav   — music bed, exactly as long as the video
    work/sfx.wav     — SFX cues placed at the times in the shot plan
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import wave

import numpy as np

SR = 44100


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def env_ad(n: int, attack: float, decay: float, sr: int = SR) -> np.ndarray:
    a = max(1, int(attack * sr))
    e = np.ones(n)
    e[:a] = np.linspace(0, 1, a)
    d = np.exp(-np.linspace(0, 1, n) * decay)
    return e * d


def lowpass(x: np.ndarray, k: int = 9) -> np.ndarray:
    if k < 2:
        return x
    kernel = np.ones(k) / k
    return np.convolve(x, kernel, mode="same")


def highpass(x: np.ndarray, k: int = 31) -> np.ndarray:
    return x - lowpass(x, k)


def noise(n: int, seed: int = 1) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.uniform(-1.0, 1.0, n)


def saw(freq, t):
    return 2.0 * ((freq * t) % 1.0) - 1.0


def note(name: str) -> float:
    """Note name → frequency, e.g. 'A2', 'C#4'."""
    names = {"C": 0, "C#": 1, "D": 2, "D#": 3, "E": 4, "F": 5, "F#": 6,
             "G": 7, "G#": 8, "A": 9, "A#": 10, "B": 11}
    if len(name) > 1 and name[1] == "#":
        pitch, octave = name[:2], int(name[2:])
    else:
        pitch, octave = name[0], int(name[1:])
    semis = names[pitch] + (octave - 4) * 12 - 9  # A4 = 440
    return 440.0 * (2 ** (semis / 12.0))


# --------------------------------------------------------------------------- #
# music
# --------------------------------------------------------------------------- #

MOODS = {
    # bpm, root, chord, kick pattern (16ths), snare pattern, drone?
    "march": dict(bpm=112, root="A2", chord=["A3", "C4", "E4"], drone=False),
    "tension": dict(bpm=88, root="D2", chord=["D3", "F3", "A3"], drone=True),
    "triumph": dict(bpm=126, root="C3", chord=["C4", "E4", "G4"], drone=False),
    "calm": dict(bpm=76, root="F2", chord=["F3", "A3", "C4"], drone=False),
    "mystery": dict(bpm=96, root="E2", chord=["E3", "G3", "B3"], drone=True),
}


def synth_music(duration: float, mood: str = "march", sr: int = SR) -> np.ndarray:
    cfg = MOODS.get(mood, MOODS["march"])
    bpm = cfg["bpm"]
    beat = 60.0 / bpm
    bar = beat * 4

    # build one bar, then tile — the loop is seamless by construction
    n_bar = int(round(bar * sr))
    t = np.arange(n_bar) / sr
    bar_buf = np.zeros(n_bar)

    root_f = note(cfg["root"])

    # ---- kick: every beat, with a slight accent on 1 ----
    for b in range(4):
        start = int(b * beat * sr)
        n = int(0.30 * sr)
        tt = np.arange(n) / sr
        f = 120 * np.exp(-tt * 28) + 48
        kick = np.sin(2 * np.pi * np.cumsum(f) / sr) * np.exp(-tt * 9)
        click = noise(n, seed=b + 1) * np.exp(-tt * 90) * 0.25
        seg = (kick + click) * (1.0 if b == 0 else 0.78)
        end = min(n_bar, start + n)
        bar_buf[start:end] += seg[: end - start]

    # ---- snare: beats 2 and 4 ----
    for b in (1, 3):
        start = int(b * beat * sr)
        n = int(0.20 * sr)
        tt = np.arange(n) / sr
        sn = (noise(n, seed=10 + b) * 0.7 + np.sin(2 * np.pi * 185 * tt) * 0.3) * np.exp(-tt * 22)
        end = min(n_bar, start + n)
        bar_buf[start:end] += sn[: end - start] * 0.55

    # ---- plucked bass: eighth notes on the root / fifth ----
    pattern = [1.0, 1.0, 1.5, 1.0, 1.0, 1.5, 1.0, 1.0]
    for i, mult in enumerate(pattern):
        start = int(i * beat / 2 * sr)
        n = int(0.26 * sr)
        tt = np.arange(n) / sr
        f = root_f * mult
        pluck = (saw(f, tt) * 0.5 + np.sin(2 * np.pi * f * tt) * 0.5) * np.exp(-tt * 7.5)
        pluck = lowpass(pluck, 7)
        end = min(n_bar, start + n)
        bar_buf[start:end] += pluck[: end - start] * 0.42

    # ---- brass pad: slow swell across the bar ----
    pad = np.zeros(n_bar)
    for i, nm in enumerate(cfg["chord"]):
        f = note(nm)
        for detune in (-0.006, 0.0, 0.006):
            pad += saw(f * (1 + detune), t + i * 0.01) * 0.12
    pad = lowpass(pad, 21)
    pad *= np.sin(np.linspace(0, np.pi, n_bar)) * 0.55
    bar_buf += pad * 0.35

    # ---- optional low drone for dramatic twists ----
    if cfg["drone"]:
        drone = (np.sin(2 * np.pi * root_f * 0.5 * t) * 0.6
                 + np.sin(2 * np.pi * root_f * 0.75 * t) * 0.3)
        drone *= 0.5 + 0.5 * np.sin(2 * np.pi * 0.08 * t)
        bar_buf += drone * 0.3

    # tile to the requested duration
    reps = int(math.ceil(duration * sr / n_bar)) + 1
    full = np.tile(bar_buf, reps)[: int(round(duration * sr))]

    # gentle top and tail so the loop never starts on a hard transient
    fade = int(0.25 * sr)
    if len(full) > 2 * fade:
        full[:fade] *= np.linspace(0, 1, fade)
        full[-fade:] *= np.linspace(1, 0, fade)

    peak = np.max(np.abs(full)) or 1.0
    full = full / peak * 0.85
    return np.stack([full, full], axis=1)


# --------------------------------------------------------------------------- #
# sound effects
# --------------------------------------------------------------------------- #


def sfx(name: str, sr: int = SR) -> np.ndarray:
    if name == "whoosh":
        n = int(0.55 * sr)
        tt = np.arange(n) / sr
        x = noise(n, seed=3)
        x = highpass(x, 41)
        e = np.exp(-((tt - 0.18) ** 2) / 0.006)
        return x * e * 0.8

    if name == "thunk":
        n = int(0.22 * sr)
        tt = np.arange(n) / sr
        x = np.sin(2 * np.pi * 90 * tt) * np.exp(-tt * 26)
        x += noise(n, seed=4) * np.exp(-tt * 90) * 0.35
        return x * 0.9

    if name == "crash":
        n = int(1.3 * sr)
        tt = np.arange(n) / sr
        x = noise(n, seed=5) * np.exp(-tt * 5.5)
        x = highpass(x, 11)
        metal = sum(np.sin(2 * np.pi * f * tt) * 0.12 for f in (620, 940, 1470, 2210))
        return (x + metal * np.exp(-tt * 7)) * 0.85

    if name == "rumble":
        n = int(2.2 * sr)
        tt = np.arange(n) / sr
        x = lowpass(noise(n, seed=6), 301)
        x = x / (np.max(np.abs(x)) or 1)
        e = np.sin(np.linspace(0, np.pi, n)) ** 1.5
        return x * e * 0.85

    if name == "scrape":
        n = int(0.7 * sr)
        tt = np.arange(n) / sr
        x = noise(n, seed=7)
        x = highpass(lowpass(x, 5), 61)
        mod = 0.5 + 0.5 * np.sin(2 * np.pi * 17 * tt)
        return x * mod * np.exp(-tt * 2.4) * 0.8

    if name == "ting":
        n = int(0.7 * sr)
        tt = np.arange(n) / sr
        x = (np.sin(2 * np.pi * 1760 * tt) + 0.45 * np.sin(2 * np.pi * 2640 * tt)) * np.exp(-tt * 9)
        return x * 0.7

    if name == "creak":
        n = int(0.8 * sr)
        tt = np.arange(n) / sr
        f = 240 + 60 * np.sin(2 * np.pi * 1.7 * tt) - 40 * tt
        x = saw(1.0, np.cumsum(f) / sr)
        x = lowpass(x, 15) * np.exp(-tt * 2.0)
        return x * 0.55

    if name == "splash":
        n = int(0.9 * sr)
        tt = np.arange(n) / sr
        x = noise(n, seed=8) * np.exp(-tt * 7)
        x = highpass(x, 5)
        bubbles = sum(np.sin(2 * np.pi * (300 + i * 120) * tt) * np.exp(-tt * (10 + i * 3)) * 0.15 for i in range(4))
        return (x * 0.7 + bubbles) * 0.85

    if name == "ambience":
        n = int(4.0 * sr)
        tt = np.arange(n) / sr
        x = lowpass(noise(n, seed=9), 401)
        x = x / (np.max(np.abs(x)) or 1)
        e = np.minimum(1, np.minimum(tt / 1.2, (n / sr - tt) / 1.2))
        return x * np.clip(e, 0, 1) * 0.5

    # unknown name → short neutral blip rather than a crash
    n = int(0.12 * sr)
    tt = np.arange(n) / sr
    return np.sin(2 * np.pi * 660 * tt) * np.exp(-tt * 30) * 0.4


SFX_NAMES = ["whoosh", "thunk", "crash", "rumble", "scrape", "ting", "creak", "splash", "ambience"]


# --------------------------------------------------------------------------- #
# cue placement
# --------------------------------------------------------------------------- #


def place_cues(plan: dict, total_duration: float, sr: int = SR, default_gain: float = 0.6) -> np.ndarray:
    n_total = int(round(total_duration * sr))
    track = np.zeros(n_total)
    fps = plan.get("fps", 30)

    for shot in plan.get("shots", []):
        start = shot.get("startFrame", 0) / fps
        for cue in shot.get("sfx", []) or []:
            if isinstance(cue, str):
                cue = {"name": cue, "at": 0.4, "gain": default_gain}
            name = cue.get("name") or cue.get("sfx")
            if name not in SFX_NAMES:
                continue
            at = start + float(cue.get("at", 0.4))
            gain = float(cue.get("gain", default_gain))
            clip = sfx(name, sr) * gain
            i0 = int(round(at * sr))
            if i0 >= n_total:
                continue
            i1 = min(n_total, i0 + len(clip))
            track[i0:i1] += clip[: i1 - i0]

    # ambience bed under the whole piece unless one was cued explicitly
    if not np.any(track):
        amb = sfx("ambience", sr) * 0.25
        reps = int(math.ceil(n_total / len(amb)))
        track = np.tile(amb, reps)[:n_total] * 0.5

    peak = np.max(np.abs(track)) or 1.0
    if peak > 1.0:
        track = track / peak * 0.95
    return np.stack([track, track], axis=1)


# --------------------------------------------------------------------------- #
# io
# --------------------------------------------------------------------------- #


def write_wav(path: str, data: np.ndarray, sr: int = SR) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    clipped = np.clip(data, -1.0, 1.0)
    ints = (clipped * 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(ints.shape[1] if ints.ndim > 1 else 1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(ints.tobytes())


def main() -> int:
    ap = argparse.ArgumentParser(description="Procedural music + SFX")
    ap.add_argument("--plan", default="work/plan.json")
    ap.add_argument("--out-dir", default="work")
    ap.add_argument("--duration", type=float, default=None, help="override duration (seconds)")
    ap.add_argument("--mood", default="march", choices=sorted(MOODS))
    ap.add_argument("--sr", type=int, default=SR)
    ap.add_argument("--no-music", action="store_true")
    ap.add_argument("--no-sfx", action="store_true")
    args = ap.parse_args()

    plan = {}
    if os.path.exists(args.plan):
        with open(args.plan, "r", encoding="utf-8") as f:
            plan = json.load(f)

    duration = args.duration
    if duration is None:
        duration = plan.get("duration") or 0
    if not duration:
        duration = 26.0
        print("[audio] no duration in plan, defaulting to 26s", file=sys.stderr)

    os.makedirs(args.out_dir, exist_ok=True)

    if not args.no_music:
        music = synth_music(duration, args.mood, args.sr)
        path = os.path.join(args.out_dir, "music.wav")
        write_wav(path, music, args.sr)
        print(f"[audio] music.wav  {duration:.2f}s  mood={args.mood}  peak={np.max(np.abs(music)):.3f}")

    if not args.no_sfx:
        cues = place_cues(plan, duration, args.sr)
        path = os.path.join(args.out_dir, "sfx.wav")
        write_wav(path, cues, args.sr)
        n_cues = sum(len(s.get("sfx", []) or []) for s in plan.get("shots", []))
        print(f"[audio] sfx.wav    {duration:.2f}s  cues={n_cues}  peak={np.max(np.abs(cues)):.3f}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
