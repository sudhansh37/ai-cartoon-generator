#!/usr/bin/env python3
"""ideas.py — generate exactly 10 original story ideas with Gemini.

Each idea carries the five fields the project requires:
title, hook, twist, why it animates well, and a truth classification
(true / disputed / folklore).

    python ai/ideas.py --theme "impossible machines" --out work/ideas.json
    python ai/ideas.py --no-ai          # deterministic offline ideas

Nothing here invents facts: ideas are explicitly fiction, and the
classification field says so when a story is folklore or disputed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gemini import GeminiClient, GeminiError  # noqa: E402

IDEA_COUNT = 10

SYSTEM = (
    "You are a short-form animation writer for vertical (9:16) cartoon shorts. "
    "You write 20-60 second narrated stories that are easy to stage with simple "
    "2D bean-head characters inside a 3D environment. You are honest about what "
    "is documented history, what is disputed, and what is folklore."
)

PROMPT = """Give me exactly {count} original story ideas for a narrated cartoon short.

Theme: {theme}
Tone: {tone}
Audience: general, short-form video (YouTube Shorts / Instagram Reels)
Each idea must work as a {dur} second narration.

Return ONLY a JSON array of {count} objects with these keys:
  "title": short punchy title (max 60 chars)
  "hook": the first line that stops the scroll (max 140 chars)
  "twist": the reveal at the end (max 200 chars)
  "why_it_animates": one sentence on why this stages well in a cartoon
    (name the environment, the characters and the camera move it needs)
  "classification": exactly one of "true", "disputed", "folklore"

Rules:
- no two ideas may share the same setting or the same twist mechanism
- "true" only for events that are well documented; if it is uncertain use
  "disputed"; if it is a myth/legend use "folklore"
- no real living people as characters
- keep every string plain text, no markdown
"""

# Deterministic offline ideas — used when there is no API key or the call fails.
# They are original fiction, so every classification is "folklore" or "true"
# only where the idea is a documented phenomenon rather than a claimed event.
OFFLINE_IDEAS = [
    {
        "title": "The Cartographer of Lost Sounds",
        "hook": "Every city has a noise it forgets, and one man collects them in jars.",
        "twist": "The jar he never opens is the sound of his own voice from before.",
        "why_it_animates": "Town at dusk, one quiet character walking between lit lamps, slow push-in.",
        "classification": "folklore",
    },
    {
        "title": "Bridge of Borrowed Steps",
        "hook": "The bridge only lets you cross if you walk in someone else's footsteps.",
        "twist": "The last set of footsteps belongs to the person waiting on the other side.",
        "why_it_animates": "Stone bridge over water, two characters swapping a walk cycle, tracking shot.",
        "classification": "folklore",
    },
    {
        "title": "The Ship That Sailed Uphill",
        "hook": "A captain swore his ship could cross a desert, and the desert agreed.",
        "twist": "The sand was the sea the whole time, just remembering slowly.",
        "why_it_animates": "Desert with a beached ship, wide reveal, crane-up camera, sand particles.",
        "classification": "folklore",
    },
    {
        "title": "The Keeper of the Unlit Lamp",
        "hook": "The lighthouse keeper lights the lamp every night. The lamp is not a lamp.",
        "twist": "It is a window, and tonight something looks back.",
        "why_it_animates": "Night coast, one character in a doorway, slow dolly-zoom for the reveal.",
        "classification": "folklore",
    },
    {
        "title": "Ten Seconds of Rain",
        "hook": "In one valley it rains for exactly ten seconds every hundred years.",
        "twist": "A boy learns to plant a whole orchard in nine of them.",
        "why_it_animates": "Mountains, rain particles, fast run cycle, reaction close-up on the last drop.",
        "classification": "folklore",
    },
    {
        "title": "The Machine That Answered Backwards",
        "hook": "Ask it any question and it answers the one you will ask next week.",
        "twist": "The last answer is the question it was built to avoid.",
        "why_it_animates": "Laboratory with beakers and screens, orbit camera, smug then scared expression.",
        "classification": "folklore",
    },
    {
        "title": "Palace of Small Doors",
        "hook": "The palace has a thousand doors and only one of them is the right size.",
        "twist": "The right door was built for the person who never grew up.",
        "why_it_animates": "Marble palace, columns and domes, low crane-down into a tiny doorway.",
        "classification": "folklore",
    },
    {
        "title": "The Town That Practised Night",
        "hook": "Once a year the whole town rehearses the dark, just in case.",
        "twist": "This year the rehearsal does not end when the sun comes up.",
        "why_it_animates": "Town street with lamps switching off in sequence, pan-right, dust VFX.",
        "classification": "folklore",
    },
    {
        "title": "The Boy Who Traded His Shadow",
        "hook": "He sold his shadow for a map, and the map only works at noon.",
        "twist": "The map leads to the person who bought the shadow.",
        "why_it_animates": "Road and field, two characters, hard noon lighting, tracking shot.",
        "classification": "folklore",
    },
    {
        "title": "The Last Stone of the Wall",
        "hook": "A wall was built to keep one thing out, and it is missing a stone.",
        "twist": "The missing stone is the one the village walks on every day.",
        "why_it_animates": "Castle wall and towers, reveal push-in, surprised reaction close-up.",
        "classification": "folklore",
    },
]


def offline_ideas(theme: str, tone: str) -> list:
    ideas = []
    for i, base in enumerate(OFFLINE_IDEAS[:IDEA_COUNT]):
        idea = dict(base)
        if theme:
            idea["why_it_animates"] = f"{idea['why_it_animates']} Theme note: {theme}."
        ideas.append(idea)
    return ideas


def coerce_ideas(raw, theme: str, tone: str) -> list:
    """Accept the model's list, keep only well-formed ideas, top up if short."""
    out = []
    if isinstance(raw, dict):
        raw = raw.get("ideas") or raw.get("stories") or []
    if not isinstance(raw, list):
        raw = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", "")).strip()
        if not title:
            continue
        cls = str(item.get("classification", "folklore")).strip().lower()
        if cls not in ("true", "disputed", "folklore"):
            cls = "folklore"
        out.append({
            "title": title[:90],
            "hook": str(item.get("hook", "")).strip()[:220],
            "twist": str(item.get("twist", "")).strip()[:260],
            "why_it_animates": str(item.get("why_it_animates") or item.get("whyItAnimates") or "").strip()[:400],
            "classification": cls,
        })
    if len(out) < IDEA_COUNT:
        for extra in offline_ideas(theme, tone):
            if len(out) >= IDEA_COUNT:
                break
            if all(extra["title"] != o["title"] for o in out):
                out.append(extra)
    return out[:IDEA_COUNT]


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate 10 cartoon short story ideas")
    ap.add_argument("--theme", default="original folk-fantasy stories")
    ap.add_argument("--tone", default="cinematic, warm, a little eerie")
    ap.add_argument("--duration", type=int, default=26, help="target narration length in seconds")
    ap.add_argument("--out", default="work/ideas.json")
    ap.add_argument("--no-ai", action="store_true", help="skip Gemini, use the offline set")
    args = ap.parse_args()

    ideas = None
    source = "offline"
    if not args.no_ai:
        client = GeminiClient()
        if client.available:
            try:
                raw = client.generate_json(
                    PROMPT.format(count=IDEA_COUNT, theme=args.theme, tone=args.tone, dur=args.duration),
                    system=SYSTEM,
                )
                ideas = coerce_ideas(raw, args.theme, args.tone)
                source = "gemini"
            except GeminiError as exc:
                print(f"[ideas] Gemini failed ({exc}); using offline ideas", file=sys.stderr)
        else:
            print("[ideas] GEMINI_API_KEY not set; using offline ideas", file=sys.stderr)

    if not ideas:
        ideas = offline_ideas(args.theme, args.tone)

    payload = {
        "theme": args.theme,
        "tone": args.tone,
        "duration": args.duration,
        "source": source,
        "count": len(ideas),
        "ideas": ideas,
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

    print(f"[ideas] {len(ideas)} ideas ({source}) → {args.out}\n")
    for i, idea in enumerate(ideas, 1):
        print(f"{i:2d}. {idea['title']}  [{idea['classification']}]")
        print(f"    hook : {idea['hook']}")
        print(f"    twist: {idea['twist']}")
        print(f"    anim : {idea['why_it_animates']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
