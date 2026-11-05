#!/usr/bin/env python3
"""timing.py — voiceover timing analysis and phrase alignment.

The video must follow the user's voiceover exactly, so this module never
retimes audio. It only measures it:

  1. duration of the voiceover
  2. where speech happens (Whisper word timestamps when available, otherwise an
     energy-based speech detector that needs nothing but the stdlib)
  3. which words belong to which sentence of the final script
  4. the frame boundaries the renderer must hit

Output: work/phrases.json

    python tools/timing.py --voice assets/voice.wav --script assets/script.txt \
                           --out work/phrases.json --fps 30
"""

from __future__ import annotations

import argparse
import array
import json
import math
import os
import re
import shutil
import subprocess
import sys
import wave
from dataclasses import dataclass, asdict
from typing import List, Tuple

# --------------------------------------------------------------------------- #
# audio loading
# --------------------------------------------------------------------------- #


@dataclass
class Audio:
    samples: List[float]          # mono, -1..1
    sr: int

    @property
    def duration(self) -> float:
        return len(self.samples) / float(self.sr)


def _decode_with_ffmpeg(path: str, sr: int = 16000) -> Audio:
    """Convert any audio file to mono float samples using ffmpeg."""
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg not found and file is not a WAV")
    cmd = [
        "ffmpeg", "-v", "error", "-i", path,
        "-f", "s16le", "-acodec", "pcm_s16le", "-ac", "1", "-ar", str(sr), "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg could not decode {path}: {proc.stderr.decode('utf8', 'ignore')[:400]}")
    raw = array.array("h")
    raw.frombytes(proc.stdout)
    samples = [v / 32768.0 for v in raw]
    return Audio(samples=samples, sr=sr)


def load_audio(path: str, target_sr: int = 16000) -> Audio:
    if not os.path.exists(path):
        raise FileNotFoundError(f"voiceover not found: {path}")
    ext = os.path.splitext(path)[1].lower()
    if ext == ".wav":
        try:
            with wave.open(path, "rb") as w:
                n = w.getnframes()
                ch = w.getnchannels()
                sw = w.getsampwidth()
                sr = w.getframerate()
                raw = w.readframes(n)
            if sw == 1:
                data = [(b - 128) / 128.0 for b in raw]
            elif sw == 2:
                a = array.array("h")
                a.frombytes(raw)
                data = [v / 32768.0 for v in a]
            elif sw == 4:
                a = array.array("i")
                a.frombytes(raw)
                data = [v / 2147483648.0 for v in a]
            else:
                raise RuntimeError(f"unsupported sample width {sw}")
            if ch > 1:
                data = [sum(data[i:i + ch]) / ch for i in range(0, len(data) - ch + 1, ch)]
            if sr != target_sr:
                data = _resample_linear(data, sr, target_sr)
                sr = target_sr
            return Audio(samples=data, sr=sr)
        except wave.Error:
            pass  # fall through to ffmpeg
    return _decode_with_ffmpeg(path, target_sr)


def _resample_linear(data: List[float], src_sr: int, dst_sr: int) -> List[float]:
    if src_sr == dst_sr or not data:
        return data
    ratio = dst_sr / float(src_sr)
    out_len = int(len(data) * ratio)
    out = [0.0] * out_len
    for i in range(out_len):
        pos = i / ratio
        i0 = int(pos)
        i1 = min(i0 + 1, len(data) - 1)
        frac = pos - i0
        out[i] = data[i0] * (1 - frac) + data[i1] * frac
    return out


# --------------------------------------------------------------------------- #
# speech detection (energy based fallback — stdlib only)
# --------------------------------------------------------------------------- #


def detect_speech_spans(audio: Audio, frame_ms: int = 20, min_gap: float = 0.14,
                        min_len: float = 0.10) -> List[Tuple[float, float]]:
    """Return [(start, end)] of voiced regions, in seconds."""
    win = max(1, int(audio.sr * frame_ms / 1000.0))
    rms: List[float] = []
    for i in range(0, len(audio.samples) - win + 1, win):
        chunk = audio.samples[i:i + win]
        s = 0.0
        for v in chunk:
            s += v * v
        rms.append(math.sqrt(s / win))

    if not rms:
        return []

    peak = max(rms)
    if peak <= 1e-6:
        return []

    # Noise floor from a low percentile, but the threshold is bounded relative
    # to the peak: a file that is almost all speech must not push the threshold
    # above the speech itself.
    ordered = sorted(rms)
    floor = ordered[max(0, int(len(ordered) * 0.10))]
    thr = min(max(floor * 2.5, peak * 0.05), peak * 0.40)
    thr = max(thr, 0.004)

    voiced = [i for i, v in enumerate(rms) if v > thr]
    if not voiced:
        return [(0.0, audio.duration)]

    step = frame_ms / 1000.0
    spans: List[Tuple[float, float]] = []
    start = voiced[0]
    prev = voiced[0]
    for idx in voiced[1:]:
        if (idx - prev) * step > min_gap:
            spans.append((start * step, (prev + 1) * step))
            start = idx
        prev = idx
    spans.append((start * step, (prev + 1) * step))

    # drop tiny blips, then merge
    spans = [s for s in spans if s[1] - s[0] >= min_len]
    merged: List[Tuple[float, float]] = []
    for s in spans:
        if merged and s[0] - merged[-1][1] < min_gap:
            merged[-1] = (merged[-1][0], s[1])
        else:
            merged.append(s)
    return merged or [(0.0, audio.duration)]


# --------------------------------------------------------------------------- #
# optional whisper alignment
# --------------------------------------------------------------------------- #


def whisper_words(path: str, model_size: str = "tiny") -> List[dict] | None:
    """Word-level timestamps with faster-whisper, or None if unavailable."""
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except Exception:
        return None
    try:
        model = WhisperModel(model_size, device="cpu", compute_type="int8")
        segments, _info = model.transcribe(path, word_timestamps=True, vad_filter=True)
        words: List[dict] = []
        for seg in segments:
            for w in (seg.words or []):
                text = (w.word or "").strip()
                if not text:
                    continue
                words.append({"word": text, "start": float(w.start), "end": float(w.end)})
        return words or None
    except Exception as exc:  # model download / runtime failure → fallback
        print(f"[timing] whisper unavailable ({exc}); using energy-based alignment", file=sys.stderr)
        return None


# --------------------------------------------------------------------------- #
# script + alignment
# --------------------------------------------------------------------------- #

SENT_SPLIT = re.compile(r"(?<=[.!?…])\s+")


def split_sentences(text: str) -> List[str]:
    text = re.sub(r"\s+", " ", text.strip())
    if not text:
        return []
    parts = [p.strip() for p in SENT_SPLIT.split(text) if p.strip()]
    # keep very short trailing fragments with the previous sentence
    out: List[str] = []
    for p in parts:
        if out and len(p.split()) < 2:
            out[-1] = out[-1] + " " + p
        else:
            out.append(p)
    return out


def word_weight(word: str) -> float:
    """Rough spoken length of a word (syllable-ish), used for proportional timing."""
    w = re.sub(r"[^A-Za-z0-9']", "", word)
    if not w:
        return 0.4
    vowels = len(re.findall(r"[aeiouyAEIOUY]+", w))
    return max(0.6, 0.55 + 0.42 * max(1, vowels)) + 0.06 * len(w)


def align(sentences: List[str], spans: List[Tuple[float, float]],
          whisper: List[dict] | None = None) -> List[dict]:
    """Map sentences onto the spoken regions of the audio."""
    if whisper:
        return _align_from_whisper(sentences, whisper)
    return _align_proportional(sentences, spans)


def _align_from_whisper(sentences: List[str], words: List[dict]) -> List[dict]:
    """Walk the recognised words, consuming one script sentence at a time."""
    out: List[dict] = []
    wi = 0
    total = len(words)
    for si, sent in enumerate(sentences):
        want = max(1, len(sent.split()))
        remaining_sentences = len(sentences) - si - 1
        budget = max(1, min(want, total - wi - remaining_sentences))
        chunk = words[wi:wi + budget]
        if not chunk:
            last_end = out[-1]["end"] if out else 0.0
            out.append({"text": sent, "start": last_end, "end": last_end + max(1.0, want * 0.4), "words": []})
            continue
        wi += len(chunk)
        out.append({
            "text": sent,
            "start": chunk[0]["start"],
            "end": chunk[-1]["end"],
            "words": [{"word": c["word"], "start": c["start"], "end": c["end"]} for c in chunk],
            "source": "whisper",
        })
    return out


def _align_proportional(sentences: List[str], spans: List[Tuple[float, float]]) -> List[dict]:
    """Distribute sentences across the detected speech spans, weighted by length."""
    if not sentences:
        return []
    if not spans:
        return []

    total_speech = sum(e - s for s, e in spans)
    weights = [sum(word_weight(w) for w in s.split()) for s in sentences]
    total_w = sum(weights) or 1.0

    # build a flat list of spoken instants: (t, is_speech)
    out: List[dict] = []
    cursor_speech = 0.0  # position within total spoken time

    def speech_to_wall(t_speech: float) -> float:
        acc = 0.0
        for s, e in spans:
            d = e - s
            if t_speech <= acc + d:
                return s + (t_speech - acc)
            acc += d
        return spans[-1][1]

    for sent, w in zip(sentences, weights):
        d = total_speech * (w / total_w)
        start = speech_to_wall(cursor_speech)
        end = speech_to_wall(min(total_speech, cursor_speech + d))
        cursor_speech += d

        # word level split inside the sentence
        words = sent.split()
        ww = [word_weight(x) for x in words]
        tot = sum(ww) or 1.0
        wlist = []
        acc = start
        for word, weight in zip(words, ww):
            span = (end - start) * (weight / tot)
            wlist.append({"word": word, "start": round(acc, 3), "end": round(acc + span, 3)})
            acc += span

        out.append({
            "text": sent,
            "start": round(start, 3),
            "end": round(end, 3),
            "words": wlist,
            "source": "energy",
        })
    return out


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #


def build_phrases(voice: str, script: str, fps: int, whisper_model: str | None,
                  pad_start: float, pad_end: float) -> dict:
    audio = load_audio(voice)
    duration = audio.duration

    spans = detect_speech_spans(audio)
    print(f"[timing] audio {duration:.2f}s, {len(spans)} speech spans, first at {spans[0][0]:.2f}s")

    words = None
    if whisper_model:
        words = whisper_words(voice, whisper_model)
        if words:
            print(f"[timing] whisper gave {len(words)} word timestamps")
        else:
            print("[timing] no whisper timestamps, falling back to energy alignment")

    sentences = split_sentences(script) if script else []
    if not sentences and words:
        # No script supplied: rebuild sentence-sized phrases from the transcript,
        # breaking on punctuation and otherwise every ~9 words, so the planner
        # still gets several shots instead of one long one.
        sentences = []
        chunk: list = []
        for w in words:
            chunk.append(w["word"])
            ends_sentence = w["word"].strip().endswith((".", "!", "?", "…"))
            if ends_sentence or len(chunk) >= 9:
                sentences.append(" ".join(chunk))
                chunk = []
        if chunk:
            sentences.append(" ".join(chunk))

    phrases = align(sentences, spans, words)

    # clamp inside the audio and never reorder
    last_end = 0.0
    for ph in phrases:
        ph["start"] = max(ph["start"], last_end)
        ph["end"] = min(max(ph["end"], ph["start"] + 0.25), duration)
        last_end = ph["end"]

    total_frames = max(1, int(round(duration * fps)))

    # frame boundaries, with a small lead-in/lead-out that never clips speech
    start_pad = int(round(pad_start * fps))
    end_pad = int(round(pad_end * fps))
    for i, ph in enumerate(phrases):
        sf = int(round(ph["start"] * fps))
        ef = int(round(ph["end"] * fps))
        if i == 0:
            sf = max(0, sf - start_pad)
        if i == len(phrases) - 1:
            ef = min(total_frames, ef + end_pad)
        ph["startFrame"] = sf
        ph["endFrame"] = max(sf + 1, ef)

    # make the shot timeline contiguous
    for i in range(1, len(phrases)):
        phrases[i]["startFrame"] = max(phrases[i]["startFrame"], phrases[i - 1]["endFrame"])
        phrases[i]["endFrame"] = max(phrases[i]["endFrame"], phrases[i]["startFrame"] + 1)

    if phrases:
        phrases[-1]["endFrame"] = total_frames

    return {
        "voice": voice,
        "duration": round(duration, 3),
        "fps": fps,
        "totalFrames": total_frames,
        "speechSpans": [[round(s, 3), round(e, 3)] for s, e in spans],
        "aligner": "whisper" if words else "energy",
        "phrases": phrases,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Voiceover timing + phrase alignment")
    ap.add_argument("--voice", required=True, help="voiceover file (wav/mp3/m4a)")
    ap.add_argument("--script", default="", help="final narration script (txt) for alignment")
    ap.add_argument("--out", default="work/phrases.json")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--whisper", default="tiny", help="faster-whisper model size, or 'off'")
    ap.add_argument("--pad-start", type=float, default=0.15, help="seconds of lead-in on shot 1")
    ap.add_argument("--pad-end", type=float, default=0.35, help="seconds held on the last shot")
    args = ap.parse_args()

    script = ""
    if args.script and os.path.exists(args.script):
        with open(args.script, "r", encoding="utf-8") as f:
            script = f.read().strip()
    elif args.script:
        script = args.script  # allow passing the script inline

    whisper_model = None if args.whisper.lower() in ("off", "none", "") else args.whisper

    data = build_phrases(args.voice, script, args.fps, whisper_model, args.pad_start, args.pad_end)

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"[timing] {data['totalFrames']} frames @ {data['fps']}fps "
          f"({data['duration']:.2f}s), {len(data['phrases'])} phrases, aligner={data['aligner']}")
    for ph in data["phrases"][:6]:
        print(f"   {ph['startFrame']:5d}-{ph['endFrame']:5d}  {ph['text'][:70]}")
    if len(data["phrases"]) > 6:
        print(f"   ... {len(data['phrases']) - 6} more")
    print(f"[timing] wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
