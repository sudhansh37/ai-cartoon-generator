#!/usr/bin/env python3
"""script.py — turn a chosen idea into a narration script + character sheet.

    python ai/script.py --idea work/ideas.json --index 3 --duration 26 \
                        --out work/script.txt --story work/story.json

Outputs:
  work/script.txt   the final narration, plain text, sentence per line
  work/story.json   title + script + the 1-3 characters the animation needs

Default narration length is 20-30 s (~2.6 spoken words per second); 45-60 s is
allowed with --duration.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gemini import GeminiClient, GeminiError  # noqa: E402

WORDS_PER_SECOND = 2.6

SKINS = ["#f0c49a", "#e0a97e", "#c98a5e", "#a86b45", "#8a5233", "#f7d6b4"]
HAIRS = ["#2a1a12", "#4a2c18", "#111111", "#7a4a1e", "#b08a4a", "#8a8a8a"]
OUTFITS = ["#c0392b", "#2f6fb5", "#4a8c5a", "#8e5bb5", "#d9822b", "#3b7f8c", "#b5476f"]
STYLES = ["tunic", "coat", "dress", "armor", "apron", "robe"]
HAIR_STYLES = ["short", "long", "bun", "spiky", "curly"]
GEARS = ["none", "none", "cap", "hat", "crown", "helmet", "turban", "hood"]
ACCESSORIES = ["none", "none", "none", "glasses", "scarf", "staff", "bag"]

SYSTEM = (
    "You write narration for vertical cartoon shorts. Plain spoken English, "
    "short sentences, one visual idea per sentence, and the last sentence must "
    "land the twist on the final spoken words. No stage directions in the "
    "narration. No emojis. No markdown."
)

PROMPT = """Write the narration for this cartoon short.

Title: {title}
Hook: {hook}
Twist: {twist}
Why it animates: {why}
Target length: {duration} seconds (about {words} words, {sentences} sentences)

Also design the characters needed to stage it (1 to 3, never more).

Return ONLY this JSON object:
{{
  "title": "{title}",
  "script": "full narration as one paragraph",
  "sentences": ["sentence 1", "sentence 2", "..."],
  "characters": [
    {{
      "id": "hero",
      "name": "short display name",
      "role": "protagonist | antagonist | bystander",
      "skin": "#hex",
      "hair": "#hex",
      "hairStyle": "short|long|bun|spiky|curly|bald",
      "outfit": "#hex",
      "outfitStyle": "tunic|coat|dress|armor|apron|robe",
      "pants": "#hex",
      "shoe": "#hex",
      "headgear": "none|cap|hat|crown|helmet|turban|hood",
      "accessory": "none|glasses|scarf|staff|bag"
    }}
  ]
}}

Rules:
- {sentences} sentences, each one stageable in a single cartoon shot
- the final sentence is the twist and must be short (max 12 words)
- colours must be readable against a 3D painted background
"""


def sentences_of(text: str) -> list:
    text = re.sub(r"\s+", " ", (text or "").strip())
    if not text:
        return []
    parts = [p.strip() for p in re.split(r"(?<=[.!?…])\s+", text) if p.strip()]
    merged: list = []
    for p in parts:
        if merged and len(p.split()) < 2:
            merged[-1] = merged[-1] + " " + p
        else:
            merged.append(p)
    return merged


def deterministic_characters(seed: str) -> list:
    h = 2166136261
    for ch in seed:
        h ^= ord(ch)
        h = (h * 16777619) & 0xFFFFFFFF

    def pick(arr, salt=0):
        nonlocal h
        h = (h * 1103515245 + 12345 + salt) & 0x7FFFFFFF
        return arr[h % len(arr)]

    hero = {
        "id": "hero", "name": "Hero", "role": "protagonist",
        "skin": pick(SKINS), "hair": pick(HAIRS, 1), "hairStyle": pick(HAIR_STYLES, 2),
        "outfit": pick(OUTFITS, 3), "outfitStyle": pick(STYLES, 4),
        "pants": pick(["#2f3b52", "#3a3a3a", "#5a4632"], 5), "shoe": "#3a2a1e",
        "headgear": pick(GEARS, 6), "accessory": pick(ACCESSORIES, 7),
    }
    other = {
        "id": "keeper", "name": "Keeper", "role": "antagonist",
        "skin": pick(SKINS, 8), "hair": pick(HAIRS, 9), "hairStyle": pick(HAIR_STYLES, 10),
        "outfit": pick(OUTFITS, 11), "outfitStyle": pick(STYLES, 12),
        "pants": pick(["#2f3b52", "#3a3a3a", "#5a4632"], 13), "shoe": "#3a2a1e",
        "headgear": pick(GEARS, 14), "accessory": pick(ACCESSORIES, 15),
    }
    if other["outfit"] == hero["outfit"]:
        other["outfit"] = "#2f6fb5" if hero["outfit"] != "#2f6fb5" else "#c0392b"
    return [hero, other]


def coerce_characters(raw, seed: str) -> list:
    out = []
    if isinstance(raw, list):
        for i, c in enumerate(raw[:3]):
            if not isinstance(c, dict):
                continue
            cid = re.sub(r"[^a-z0-9_]", "", str(c.get("id") or f"char{i}").lower()) or f"char{i}"
            out.append({
                "id": cid,
                "name": str(c.get("name") or cid.title())[:40],
                "role": str(c.get("role") or "bystander"),
                "skin": _hex(c.get("skin"), "#f0c49a"),
                "hair": _hex(c.get("hair"), "#2a1a12"),
                "hairStyle": _one_of(c.get("hairStyle"), HAIR_STYLES + ["bald"], "short"),
                "outfit": _hex(c.get("outfit"), "#c0392b"),
                "outfitStyle": _one_of(c.get("outfitStyle"), STYLES, "tunic"),
                "pants": _hex(c.get("pants"), "#2f3b52"),
                "shoe": _hex(c.get("shoe"), "#3a2a1e"),
                "headgear": _one_of(c.get("headgear"), GEARS, "none"),
                "accessory": _one_of(c.get("accessory"), ACCESSORIES, "none"),
            })
    if not out:
        out = deterministic_characters(seed)
    # ids must be unique and must include a "hero"
    seen = set()
    for i, c in enumerate(out):
        base = c["id"]
        while c["id"] in seen:
            c["id"] = f"{base}{i}"
        seen.add(c["id"])
    if not any(c["id"] == "hero" for c in out):
        out[0]["id"] = "hero"
    return out


def _hex(v, default):
    s = str(v or "").strip()
    return s if re.match(r"^#[0-9a-fA-F]{6}$", s) else default


def _one_of(v, allowed, default):
    s = str(v or "").strip()
    return s if s in allowed else default


def offline_script(idea: dict, duration: int) -> str:
    """A staged, deterministic script built from the idea itself."""
    hook = idea.get("hook") or "It started with one impossible thing."
    twist = idea.get("twist") or "And it had been true all along."
    title = idea.get("title") or "Untitled"

    if duration <= 32:
        return " ".join([
            hook.rstrip(".") + ".",
            f"They called it {title.lower()}.",
            "Every night the same thing happened, and every night nobody looked.",
            "Then someone finally looked closely.",
            twist.rstrip(".") + ".",
        ])
    return " ".join([
        hook.rstrip(".") + ".",
        f"They called it {title.lower()}, and the name stuck.",
        "For years the town explained it away with weather and bad luck.",
        "Every night the same thing happened, and every night nobody looked.",
        "Then a stranger arrived with one question nobody had asked.",
        "The answer was written where everyone walked every day.",
        twist.rstrip(".") + ".",
    ])


def main() -> int:
    ap = argparse.ArgumentParser(description="Write the narration script + characters")
    ap.add_argument("--idea", default="work/ideas.json")
    ap.add_argument("--index", type=int, default=1, help="1-based idea index")
    ap.add_argument("--duration", type=int, default=26, help="target narration seconds (20-60)")
    ap.add_argument("--out", default="work/script.txt")
    ap.add_argument("--story", default="work/story.json")
    ap.add_argument("--no-ai", action="store_true")
    args = ap.parse_args()

    duration = max(10, min(90, args.duration))
    idea = {}
    if os.path.exists(args.idea):
        with open(args.idea, "r", encoding="utf-8") as f:
            data = json.load(f)
        ideas = data.get("ideas") if isinstance(data, dict) else data
        ideas = ideas or []
        if ideas:
            idx = max(1, min(len(ideas), args.index))
            idea = ideas[idx - 1]
            print(f"[script] using idea {idx}/{len(ideas)}: {idea.get('title')}")
    else:
        print(f"[script] {args.idea} not found, writing from a generic premise", file=sys.stderr)

    words = int(round(duration * WORDS_PER_SECOND))
    sentence_count = max(4, min(9, round(words / 9.0)))

    script = ""
    characters: list = []
    source = "offline"

    if not args.no_ai:
        client = GeminiClient()
        if client.available:
            try:
                raw = client.generate_json(
                    PROMPT.format(
                        title=idea.get("title", "Untitled"),
                        hook=idea.get("hook", ""),
                        twist=idea.get("twist", ""),
                        why=idea.get("why_it_animates", ""),
                        duration=duration,
                        words=words,
                        sentences=sentence_count,
                    ),
                    system=SYSTEM,
                )
                if isinstance(raw, dict):
                    sents = raw.get("sentences")
                    if isinstance(sents, list) and sents:
                        script = " ".join(str(s).strip() for s in sents if str(s).strip())
                    script = script or str(raw.get("script", "")).strip()
                    characters = coerce_characters(raw.get("characters"), idea.get("title", "story"))
                    source = "gemini"
            except GeminiError as exc:
                print(f"[script] Gemini failed ({exc}); using offline script", file=sys.stderr)
        else:
            print("[script] GEMINI_API_KEY not set; using offline script", file=sys.stderr)

    if not script:
        script = offline_script(idea, duration)
    if not characters:
        characters = deterministic_characters(idea.get("title", "story"))

    sents = sentences_of(script)
    wc = len(script.split())

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(script.strip() + "\n")

    story = {
        "title": idea.get("title", "Untitled"),
        "hook": idea.get("hook", ""),
        "twist": idea.get("twist", ""),
        "classification": idea.get("classification", "folklore"),
        "duration": duration,
        "script": script.strip(),
        "sentences": sents,
        "characters": characters,
        "source": source,
    }
    with open(args.story, "w", encoding="utf-8") as f:
        json.dump(story, f, indent=2, ensure_ascii=False)

    print(f"[script] {wc} words, {len(sents)} sentences, {len(characters)} characters ({source})")
    print(f"[script] ≈{wc / WORDS_PER_SECOND:.1f}s at {WORDS_PER_SECOND} words/s (target {duration}s)")
    for i, s in enumerate(sents, 1):
        print(f"  {i}. {s}")
    print(f"[script] wrote {args.out} and {args.story}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
