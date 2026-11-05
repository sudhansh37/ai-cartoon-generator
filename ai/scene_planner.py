#!/usr/bin/env python3
"""scene_planner.py — build the shot plan (PLAN.md + work/plan.json).

Every sentence becomes exactly one shot, timed to the voiceover frames produced
by tools/timing.py. Each shot specifies what the spec demands: start/end frames,
spoken words, environment, lighting, camera move, character pose, expression,
props, visual effects and sound cues.

Gemini designs the shots when a key is available; a deterministic rule-based
planner does the same job offline, so the pipeline always produces a plan.

    python ai/scene_planner.py --script work/script.txt --phrases work/phrases.json \
                               --story work/story.json --out work/plan.json --plan-md PLAN.md
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gemini import GeminiClient, GeminiError  # noqa: E402

ENVIRONMENTS = ["town", "bridge", "ship", "castle", "desert", "lab", "palace",
                "mountains", "house", "road", "trees", "water", "clouds", "plain"]
LIGHTING = ["dawn", "day", "dusk", "night", "storm", "interior", "magic"]
SHOT_SIZES = ["wide", "medium", "close-up", "extreme-close-up", "reaction", "environment"]
CAMERA_MOVES = ["static", "push-in", "pull-out", "pan-left", "pan-right", "crane-up",
                "crane-down", "orbit", "tracking", "reveal", "tilt-up", "dolly-zoom"]
POSES = ["stand", "walk", "run", "sit", "point", "react", "wave", "carry"]
EXPRESSIONS = ["neutral", "happy", "sad", "angry", "surprised", "scared", "smug", "determined"]
VFX = ["dust", "impact", "sparkle", "rain", "smoke", "confetti"]
SFX = ["whoosh", "thunk", "crash", "rumble", "scrape", "ting", "creak", "splash", "ambience"]

ENV_KEYWORDS = [
    ("ship", ["ship", "sail", "captain", "harbour", "harbor", "deck", "voyage", "boat"]),
    ("bridge", ["bridge", "cross", "arch", "span", "footstep"]),
    ("castle", ["castle", "wall", "knight", "king", "queen", "gate", "keep", "tower"]),
    ("palace", ["palace", "throne", "court", "marble", "royal"]),
    ("desert", ["desert", "sand", "dune", "cactus", "mirage"]),
    ("lab", ["lab", "machine", "experiment", "inventor", "beaker", "circuit", "engine"]),
    ("mountains", ["mountain", "peak", "cliff", "valley", "snow"]),
    ("water", ["river", "lake", "sea", "ocean", "water", "wave", "shore", "rain"]),
    ("clouds", ["sky", "cloud", "fly", "flying", "float", "above"]),
    ("trees", ["forest", "tree", "woods", "jungle"]),
    ("road", ["road", "street", "car", "journey", "travel", "highway"]),
    ("house", ["house", "home", "door", "room", "kitchen", "window", "roof"]),
    ("town", ["town", "village", "city", "market", "lamp", "crowd", "people", "shop"]),
]

LIGHT_KEYWORDS = [
    ("night", ["night", "dark", "midnight", "moon", "shadow"]),
    ("dawn", ["dawn", "morning", "sunrise", "first light"]),
    ("dusk", ["dusk", "evening", "sunset", "twilight"]),
    ("storm", ["storm", "thunder", "wind", "grey", "gray"]),
    ("magic", ["magic", "glow", "spell", "impossible", "shimmer"]),
    ("interior", ["inside", "laboratory", "room", "hall"]),
]

ENV_SFX = {
    "town": "ambience", "bridge": "thunk", "ship": "splash", "castle": "rumble",
    "palace": "ting", "desert": "whoosh", "lab": "ting", "mountains": "whoosh",
    "house": "creak", "road": "whoosh", "trees": "ambience", "water": "splash",
    "clouds": "whoosh", "plain": "ambience",
}

SYSTEM = (
    "You are a film shot planner for vertical cartoon shorts. You stage one "
    "sentence per shot, choose a different shot size for each beat, and make the "
    "final shot land the twist on the final spoken words. You answer with JSON only."
)

PROMPT = """Design the shot list for this cartoon short.

TITLE: {title}
CHARACTERS: {characters}
SHOTS AVAILABLE (one per sentence, in order):
{phrases}

Allowed values — use only these exact strings:
  environment: {envs}
  lighting: {lighting}
  shotSize: {shot_sizes}
  camera.move: {camera_moves}
  pose: {poses}
  expression: {expressions}
  vfx: {vfx}
  sfx name: {sfx}

Return ONLY this JSON object:
{{
  "music": "march|tension|triumph|calm|mystery",
  "shots": [
    {{
      "environment": "town",
      "environmentVariant": 1,
      "lighting": "dusk",
      "shotSize": "wide",
      "camera": {{"move": "push-in"}},
      "characters": [
        {{"id": "hero", "pose": "stand", "expression": "determined", "x": 0, "facing": 1, "action": "idle"}}
      ],
      "props": ["lamp"],
      "vfx": ["dust"],
      "sfx": [{{"at": 0.4, "name": "whoosh", "gain": 0.6}}]
    }}
  ]
}}

Rules:
- exactly {n} shots, one per sentence, in the same order
- illustrate each sentence literally, but vary shot size: no more than two shots
  of the same size in a row
- use at least THREE different environments across the piece — do not put every
  shot in the same room or street; a change of environment is what makes the
  cut feel like a film
- the first shot establishes the world (wide or environment)
- the last shot is a close-up or reaction and holds on the twist
- characters[].id must be one of: {ids}
- x is metres from centre (-2.5 to 2.5); facing is 1 or -1
- at most 2 sfx per shot, "at" is seconds from the start of the shot
- every shot needs at least one character unless it is an environment shot
"""


# --------------------------------------------------------------------------- #
# rule-based planner
# --------------------------------------------------------------------------- #


def guess_environment(text: str) -> str:
    low = text.lower()
    for env, words in ENV_KEYWORDS:
        if any(w in low for w in words):
            return env
    return "town"


def guess_lighting(text: str, index: int, total: int) -> str:
    low = text.lower()
    for light, words in LIGHT_KEYWORDS:
        if any(w in low for w in words):
            return light
    # a gentle time-of-day arc so the piece does not look flat
    if total <= 1:
        return "day"
    frac = index / max(1, total - 1)
    if frac < 0.25:
        return "dawn"
    if frac < 0.7:
        return "day"
    if frac < 0.9:
        return "dusk"
    return "night"


def guess_pose(text: str) -> str:
    low = text.lower()
    if re.search(r"\brun|running|ran\b|flee|chase", low):
        return "run"
    if re.search(r"\bwalk|walked|walking|step|cross", low):
        return "walk"
    if re.search(r"\bpoint|pointed|show|showed|look at", low):
        return "point"
    if re.search(r"\bsit|sat|sitting|wait|waiting", low):
        return "sit"
    if re.search(r"\bgasp|shock|stare|stared|turned|turn\b", low):
        return "react"
    return "stand"


def guess_expression(text: str, is_last: bool) -> str:
    low = text.lower()
    if "!" in text:
        return "surprised"
    if re.search(r"\bfear|afraid|scared|terror|horror", low):
        return "scared"
    if re.search(r"\bsad|grief|lost|cry|tears", low):
        return "sad"
    if re.search(r"\banger|angry|furious|rage|shout", low):
        return "angry"
    if re.search(r"\blaugh|happy|smile|joy|delight", low):
        return "happy"
    if re.search(r"\bsmile|smirk|knew|clever|finally", low):
        return "smug"
    if is_last:
        return "determined"
    return "neutral"


def rule_based_shot(text: str, index: int, total: int, characters: list) -> dict:
    env = guess_environment(text)
    is_last = index == total - 1
    is_first = index == 0

    if is_first:
        shot_size = "wide"
    elif is_last:
        shot_size = "close-up"
    else:
        cycle = ["medium", "close-up", "reaction", "medium", "environment"]
        shot_size = cycle[(index - 1) % len(cycle)]

    move_cycle = ["push-in", "tracking", "pan-right", "crane-up", "orbit",
                  "reveal", "tilt-up", "pan-left", "push-in", "dolly-zoom"]
    move = "dolly-zoom" if is_last else move_cycle[index % len(move_cycle)]
    if is_first:
        move = "reveal"

    hero = next((c for c in characters if c.get("id") == "hero"), characters[0] if characters else {"id": "hero"})
    cast = [{
        "id": hero["id"],
        "pose": guess_pose(text),
        "expression": guess_expression(text, is_last),
        "x": 0.0,
        "facing": 1,
        "action": "idle" if shot_size != "wide" else "walk-in",
    }]

    # a second character on alternating shots keeps the frame alive
    if len(characters) > 1 and index % 2 == 1 and shot_size in ("medium", "reaction"):
        other = next(c for c in characters if c["id"] != hero["id"])
        cast.append({
            "id": other["id"],
            "pose": "stand",
            "expression": "surprised" if is_last else "neutral",
            "x": -1.7,
            "facing": 1,
            "action": "idle",
        })

    vfx = []
    low = text.lower()
    if any(w in low for w in ("dust", "old", "abandoned", "desert", "dry")):
        vfx.append("dust")
    if any(w in low for w in ("rain", "storm", "tears")):
        vfx.append("rain")
    if any(w in low for w in ("glow", "magic", "light", "shimmer", "star")):
        vfx.append("sparkle")
    if any(w in low for w in ("crash", "explode", "impact", "slam", "hit")):
        vfx.append("impact")
    if any(w in low for w in ("smoke", "fog", "mist", "steam")):
        vfx.append("smoke")
    if is_last:
        vfx.append("sparkle")
    vfx = vfx[:2] or ["dust"]

    sfx = [{"at": 0.3, "name": ENV_SFX.get(env, "ambience"), "gain": 0.45}]
    if is_last:
        sfx.append({"at": 0.1, "name": "ting", "gain": 0.6})
    elif index % 3 == 0:
        sfx.append({"at": 0.5, "name": "whoosh", "gain": 0.35})

    return {
        "environment": env,
        "environmentVariant": 1 + (index % 3),
        "lighting": guess_lighting(text, index, total),
        "shotSize": shot_size,
        "camera": {"move": move, "easing": "easeInOut"},
        "characters": cast,
        "props": [],
        "vfx": vfx,
        "sfx": sfx,
    }


# --------------------------------------------------------------------------- #
# coercion
# --------------------------------------------------------------------------- #


def one_of(value, allowed, default):
    s = str(value or "").strip()
    return s if s in allowed else default


def coerce_shot(raw: dict, index: int, total: int, characters: list, text: str) -> dict:
    fallback = rule_based_shot(text, index, total, characters)
    if not isinstance(raw, dict):
        return fallback

    env = one_of(raw.get("environment"), ENVIRONMENTS, fallback["environment"])
    shot_size = one_of(raw.get("shotSize"), SHOT_SIZES, fallback["shotSize"])
    lighting = one_of(raw.get("lighting"), LIGHTING, fallback["lighting"])

    cam_raw = raw.get("camera") if isinstance(raw.get("camera"), dict) else {}
    move = one_of(cam_raw.get("move"), CAMERA_MOVES, fallback["camera"]["move"])

    ids = {c.get("id") for c in characters}
    cast = []
    for c in (raw.get("characters") or [])[:3]:
        if not isinstance(c, dict):
            continue
        cid = str(c.get("id") or "hero")
        if cid not in ids:
            cid = "hero" if "hero" in ids else (next(iter(ids)) if ids else "hero")
        cast.append({
            "id": cid,
            "pose": one_of(c.get("pose"), POSES, "stand"),
            "expression": one_of(c.get("expression"), EXPRESSIONS, "neutral"),
            "x": _clamp(_num(c.get("x"), 0.0), -2.5, 2.5),
            "z": _clamp(_num(c.get("z"), 0.0), -1.5, 1.5),
            "facing": 1 if _num(c.get("facing"), 1) >= 0 else -1,
            "action": one_of(c.get("action"), ["idle", "walk-in", "walk-out", "drift", "pop"], "idle"),
        })
    if not cast:
        cast = fallback["characters"]

    vfx = [v for v in (raw.get("vfx") or []) if v in VFX][:2] or fallback["vfx"]

    sfx = []
    for cue in (raw.get("sfx") or [])[:3]:
        if isinstance(cue, str):
            cue = {"at": 0.4, "name": cue, "gain": 0.5}
        if not isinstance(cue, dict):
            continue
        name = one_of(cue.get("name"), SFX, "")
        if not name:
            continue
        sfx.append({
            "at": round(_clamp(_num(cue.get("at"), 0.4), 0.0, 8.0), 2),
            "name": name,
            "gain": round(_clamp(_num(cue.get("gain"), 0.5), 0.05, 1.0), 2),
        })
    if not sfx:
        sfx = fallback["sfx"]

    props = [str(p)[:40] for p in (raw.get("props") or [])][:4]

    return {
        "environment": env,
        "environmentVariant": 1 + (int(_num(raw.get("environmentVariant"), 1)) % 3),
        "lighting": lighting,
        "shotSize": shot_size,
        "camera": {"move": move, "easing": str(cam_raw.get("easing") or "easeInOut")},
        "characters": cast,
        "props": props,
        "vfx": vfx,
        "sfx": sfx,
    }


def _num(v, default):
    try:
        return float(v)
    except Exception:
        return float(default)


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


# --------------------------------------------------------------------------- #
# plan assembly
# --------------------------------------------------------------------------- #


def build_plan(story: dict, phrases: dict, shots: list, music: str, fps: int,
               width: int, height: int) -> dict:
    characters = story.get("characters") or []
    ph_list = phrases.get("phrases") or []
    total_frames = int(phrases.get("totalFrames") or 1)

    out_shots = []
    for i, ph in enumerate(ph_list):
        raw = shots[i] if i < len(shots) else None
        design = coerce_shot(raw, i, len(ph_list), characters, ph.get("text", ""))
        out_shots.append({
            "index": i,
            "startFrame": int(ph.get("startFrame", 0)),
            "endFrame": int(ph.get("endFrame", 0)),
            "words": ph.get("text", ""),
            "text": ph.get("text", ""),
            "shotSize": design["shotSize"],
            "environment": design["environment"],
            "environmentVariant": design["environmentVariant"],
            "lighting": design["lighting"],
            "camera": design["camera"],
            "characters": design["characters"],
            "props": design["props"],
            "vfx": design["vfx"],
            "sfx": design["sfx"],
            "music": music,
            "transition": "cut",
        })

    # keep the timeline strictly contiguous and ending on the last frame
    prev = 0
    for s in out_shots:
        s["startFrame"] = max(prev, s["startFrame"])
        s["endFrame"] = max(s["startFrame"] + 1, s["endFrame"])
        prev = s["endFrame"]
    if out_shots:
        out_shots[-1]["endFrame"] = max(out_shots[-1]["startFrame"] + 1, total_frames)

    return {
        "title": story.get("title", "Untitled"),
        "fps": fps,
        "width": width,
        "height": height,
        "duration": float(phrases.get("duration") or (total_frames / fps)),
        "totalFrames": total_frames,
        "style": "flat-2d-bean-head-in-3d-painted-world",
        "audio": {"voice": "assets/voice.wav", "music": music},
        "render": {"postprocessing": True, "depthOfField": True, "inkOutlines": True, "shadowQuality": 1024},
        "characters": characters,
        "shots": out_shots,
    }


def write_plan_md(plan: dict, path: str, source: str) -> None:
    lines = []
    lines.append(f"# PLAN — {plan['title']}\n")
    lines.append(f"- Duration: **{plan['duration']:.2f}s** ({plan['totalFrames']} frames @ {plan['fps']} fps)")
    lines.append(f"- Output: {plan['width']}x{plan['height']}, H.264 + AAC")
    lines.append(f"- Planner: {source}")
    lines.append(f"- Music: {plan['audio'].get('music')}\n")

    lines.append("## Characters\n")
    lines.append("| id | name | skin | hair | outfit | headgear | accessory |")
    lines.append("|---|---|---|---|---|---|---|")
    for c in plan["characters"]:
        lines.append(
            f"| `{c['id']}` | {c.get('name','')} | {c.get('skin','')} | {c.get('hair','')} | "
            f"{c.get('outfit','')} / {c.get('outfitStyle','')} | {c.get('headgear','')} | {c.get('accessory','')} |"
        )

    lines.append("\n## Shots\n")
    lines.append("| # | frames | words | size | environment | light | camera | pose | expression | props | vfx | sfx |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for s in plan["shots"]:
        cast = s["characters"][0] if s["characters"] else {}
        sfx = ", ".join(f"{c['name']}@{c['at']}s" for c in s["sfx"])
        lines.append(
            f"| {s['index']+1} | {s['startFrame']}–{s['endFrame']} | {s['words']} | {s['shotSize']} | "
            f"{s['environment']} v{s['environmentVariant']} | {s['lighting']} | {s['camera']['move']} | "
            f"{cast.get('pose','—')} | {cast.get('expression','—')} | "
            f"{', '.join(s['props']) or '—'} | {', '.join(s['vfx']) or '—'} | {sfx or '—'} |"
        )

    total_sfx = sum(len(s["sfx"]) for s in plan["shots"])
    lines.append(f"\n## Audio\n")
    lines.append(f"- Music bed: `{plan['audio'].get('music')}` at -6 dB under the voice")
    lines.append(f"- SFX cues: {total_sfx}, each at -6 dB with peaks capped at 0.7 x voice peak")
    lines.append(f"- The voiceover is used exactly as delivered — never retimed or re-encoded")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def validate(plan: dict) -> list:
    problems = []
    if not plan["shots"]:
        problems.append("no shots")
    if not plan["characters"]:
        problems.append("no characters")
    for s in plan["shots"]:
        if s["endFrame"] <= s["startFrame"]:
            problems.append(f"shot {s['index']}: endFrame <= startFrame")
        if not s["words"].strip():
            problems.append(f"shot {s['index']}: no spoken words")
    if plan["shots"] and plan["shots"][-1]["endFrame"] != plan["totalFrames"]:
        problems.append("timeline does not end on totalFrames")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description="Build the shot plan from the script + timing")
    ap.add_argument("--script", default="work/script.txt")
    ap.add_argument("--phrases", default="work/phrases.json")
    ap.add_argument("--story", default="work/story.json")
    ap.add_argument("--out", default="work/plan.json")
    ap.add_argument("--plan-md", default="PLAN.md")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--width", type=int, default=1080)
    ap.add_argument("--height", type=int, default=1920)
    ap.add_argument("--no-ai", action="store_true")
    args = ap.parse_args()

    if not os.path.exists(args.phrases):
        print(f"[plan] ERROR {args.phrases} missing — run tools/timing.py first", file=sys.stderr)
        return 1
    with open(args.phrases, "r", encoding="utf-8") as f:
        phrases = json.load(f)

    story = {}
    if os.path.exists(args.story):
        with open(args.story, "r", encoding="utf-8") as f:
            story = json.load(f)
    if not story.get("title"):
        story["title"] = "Untitled"

    # script-ready / voiceover-ready runs have no character sheet yet: build a
    # deterministic cast from the story title so the plan always has characters.
    if not story.get("characters"):
        try:
            from script import deterministic_characters  # same folder
            story["characters"] = deterministic_characters(story["title"])
        except Exception:
            story["characters"] = [{"id": "hero", "name": "Hero", "role": "protagonist"}]
        print(f"[plan] no character sheet found — generated {len(story['characters'])} characters")
        with open(args.story, "w", encoding="utf-8") as f:
            json.dump(story, f, indent=2, ensure_ascii=False)

    script_text = ""
    if os.path.exists(args.script):
        with open(args.script, "r", encoding="utf-8") as f:
            script_text = f.read().strip()

    ph_list = phrases.get("phrases") or []
    if not ph_list:
        print("[plan] ERROR phrases.json has no phrases", file=sys.stderr)
        return 1

    # ---- ask Gemini for the shot design ----
    shots = []
    music = "march"
    source = "rule-based"
    if not args.no_ai:
        client = GeminiClient()
        if client.available:
            phrase_block = "\n".join(
                f"{i+1}. ({p.get('startFrame')}-{p.get('endFrame')} frames) {p.get('text','')}"
                for i, p in enumerate(ph_list)
            )
            ids = ", ".join(c["id"] for c in story["characters"]) or "hero"
            char_block = "; ".join(
                f"{c['id']} ({c.get('name','')}, {c.get('role','')}, outfit {c.get('outfit','')}, "
                f"headgear {c.get('headgear','none')})"
                for c in story["characters"]
            ) or "hero (protagonist)"
            try:
                raw = client.generate_json(
                    PROMPT.format(
                        title=story["title"],
                        characters=char_block,
                        phrases=phrase_block,
                        envs=", ".join(ENVIRONMENTS),
                        lighting=", ".join(LIGHTING),
                        shot_sizes=", ".join(SHOT_SIZES),
                        camera_moves=", ".join(CAMERA_MOVES),
                        poses=", ".join(POSES),
                        expressions=", ".join(EXPRESSIONS),
                        vfx=", ".join(VFX),
                        sfx=", ".join(SFX),
                        n=len(ph_list),
                        ids=ids,
                    ),
                    system=SYSTEM,
                )
                if isinstance(raw, dict):
                    got = raw.get("shots") or []
                    if isinstance(got, list) and got:
                        shots = got
                        music = str(raw.get("music") or "march")
                        if music not in ("march", "tension", "triumph", "calm", "mystery"):
                            music = "march"
                        source = "gemini"
                if len(shots) < len(ph_list):
                    print(f"[plan] Gemini returned {len(shots)}/{len(ph_list)} shots; "
                          f"filling the rest with the rule-based planner", file=sys.stderr)
            except GeminiError as exc:
                print(f"[plan] Gemini failed ({exc}); using the rule-based planner", file=sys.stderr)
        else:
            print("[plan] GEMINI_API_KEY not set; using the rule-based planner", file=sys.stderr)

    plan = build_plan(story, phrases, shots, music, args.fps, args.width, args.height)
    problems = validate(plan)

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(plan, f, indent=2, ensure_ascii=False)
    write_plan_md(plan, args.plan_md, source)

    print(f"[plan] {len(plan['shots'])} shots, {plan['totalFrames']} frames, "
          f"{plan['duration']:.2f}s ({source})")
    for s in plan["shots"]:
        cast = s["characters"][0] if s["characters"] else {}
        print(f"  {s['startFrame']:5d}-{s['endFrame']:5d} {s['shotSize']:<12} "
              f"{s['environment']:<9} {s['lighting']:<8} {s['camera']['move']:<11} "
              f"{cast.get('pose','—'):<7} {cast.get('expression','—'):<10} {s['words'][:52]}")
    print(f"[plan] wrote {args.out} and {args.plan_md}")
    if problems:
        print(f"[plan] WARNINGS: {'; '.join(problems)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
