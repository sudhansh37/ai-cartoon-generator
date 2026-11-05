#!/usr/bin/env python3
"""fallback.py — procedural art when AI image generation is unavailable.

The project must never stop because an image service is down. This module draws
the same bean-head character cards the browser engine draws, but with Pillow, so
the pipeline always has usable art:

  * character cards      → assets/generated/char_<id>.png
  * environment backdrops → assets/generated/env_<name>.png

    python imagegen/fallback.py --story work/story.json --out assets/generated
    python imagegen/fallback.py --env town --out assets/generated
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys

from PIL import Image, ImageDraw, ImageFilter

W, H = 512, 1024
INK = (43, 23, 48)
SKINS = ["#f0c49a", "#e0a97e", "#c98a5e", "#a86b45", "#8a5233", "#f7d6b4"]
HAIRS = ["#2a1a12", "#4a2c18", "#111111", "#7a4a1e", "#b08a4a", "#8a8a8a"]
OUTFITS = ["#c0392b", "#2f6fb5", "#4a8c5a", "#8e5bb5", "#d9822b", "#3b7f8c", "#b5476f"]


def _rgb(hex_colour: str) -> tuple:
    h = hex_colour.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _shade(colour: str, amount: float) -> tuple:
    r, g, b = _rgb(colour)
    f = lambda x: max(0, min(255, int(x + (255 - x) * amount if amount >= 0 else x * (1 + amount))))
    return (f(r), f(g), f(b))


def seeded_pick(seed: str, arr: list, salt: int = 0) -> str:
    h = int(hashlib.sha256(f"{seed}:{salt}".encode()).hexdigest()[:8], 16)
    return arr[h % len(arr)]


def character_spec(char_id: str, base: dict | None = None) -> dict:
    base = base or {}
    return {
        "skin": base.get("skin") or seeded_pick(char_id, SKINS, 1),
        "hair": base.get("hair") or seeded_pick(char_id, HAIRS, 2),
        "outfit": base.get("outfit") or seeded_pick(char_id, OUTFITS, 3),
        "pants": base.get("pants") or "#2f3b52",
        "shoe": base.get("shoe") or "#3a2a1e",
    }


def draw_character_card(spec: dict, out_path: str, pose: str = "stand", expression: str = "neutral") -> str:
    """Same bean-head proportions as web/toon.js, drawn with Pillow."""
    scale = 2  # supersample for clean ink lines
    img = Image.new("RGBA", (W * scale, H * scale), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = scale
    cx = W * s // 2
    ink_w = 9 * s

    skin = _rgb(spec["skin"])
    hair = _rgb(spec["hair"])
    outfit = _rgb(spec["outfit"])
    pants = _rgb(spec["pants"])
    shoe = _rgb(spec["shoe"])

    def ellipse(box, fill, outline=INK, width=ink_w):
        d.ellipse(box, fill=fill, outline=outline, width=width)

    # ground shadow
    shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sd.ellipse([cx - 130 * s, 966 * s, cx + 130 * s, 1006 * s], fill=(20, 10, 30, 90))
    shadow = shadow.filter(ImageFilter.GaussianBlur(10 * s))
    img.alpha_composite(shadow)
    d = ImageDraw.Draw(img)

    # legs
    leg_swing = 26 * s if pose == "walk" else (48 * s if pose == "run" else 0)
    for side in (-1, 1):
        hx = cx + side * 48 * s
        fx = hx + (leg_swing * side if pose in ("walk", "run") else 0)
        d.line([hx, 780 * s, fx, 930 * s], fill=INK, width=62 * s)
        d.line([hx, 780 * s, fx, 930 * s], fill=pants, width=50 * s)
        d.rounded_rectangle(
            [fx - 44 * s, 918 * s, fx + 44 * s, 958 * s],
            radius=16 * s, fill=shoe, outline=INK, width=7 * s,
        )

    # torso
    ellipse([cx - 116 * s, 480 * s, cx + 116 * s, 830 * s], outfit)

    # collar detail
    d.arc([cx - 46 * s, 500 * s, cx + 46 * s, 570 * s], 200, 340, fill=_shade(spec["outfit"], -0.35), width=6 * s)

    # arms
    arm_up = pose in ("point", "react", "wave")
    for side in (-1, 1):
        sx = cx + side * 104 * s
        sy = 545 * s
        if arm_up and side == 1:
            ex, ey = cx + 210 * s, 360 * s
        elif arm_up and side == -1:
            ex, ey = cx - 200 * s, 400 * s
        else:
            ex, ey = sx + side * 40 * s, 700 * s
        d.line([sx, sy, ex, ey], fill=INK, width=54 * s)
        d.line([sx, sy, ex, ey], fill=outfit, width=44 * s)
        ellipse([ex - 27 * s, ey - 27 * s, ex + 27 * s, ey + 27 * s], skin, width=7 * s)

    # neck
    d.rounded_rectangle([cx - 30 * s, 405 * s, cx + 30 * s, 480 * s], radius=18 * s,
                        fill=_shade(spec["skin"], -0.12), outline=INK, width=7 * s)

    # head
    ellipse([cx - 122 * s, 173 * s, cx + 122 * s, 457 * s], skin)

    # hair
    d.chord([cx - 124 * s, 150 * s, cx + 124 * s, 400 * s], 180, 360, fill=hair, outline=INK, width=8 * s)

    # ears
    for side in (-1, 1):
        ellipse([cx + side * 118 * s - 20 * s, 300 * s, cx + side * 118 * s + 20 * s, 340 * s], skin, width=6 * s)

    # eyes
    eye_y = 321 * s
    for side in (-1, 1):
        ex = cx + side * 46 * s
        ellipse([ex - 24 * s, eye_y - 26 * s, ex + 24 * s, eye_y + 26 * s], (255, 255, 255), width=6 * s)
        d.ellipse([ex - 11 * s, eye_y - 13 * s, ex + 11 * s, eye_y + 15 * s], fill=INK)
        d.ellipse([ex - 6 * s, eye_y - 9 * s, ex - 1 * s, eye_y - 3 * s], fill=(255, 255, 255))

    # brows
    brow_drop = {"angry": 10, "sad": -8, "surprised": -14}.get(expression, 0)
    for side in (-1, 1):
        bx = cx + side * 46 * s
        d.line([bx - 26 * s, (eye_y - 46 + brow_drop) * 1 + 0, bx + 26 * s, (eye_y - 46 - brow_drop)],
               fill=_shade(spec["hair"], -0.1), width=9 * s)

    # nose
    d.arc([cx - 14 * s, eye_y + 22 * s, cx + 14 * s, eye_y + 52 * s], 20, 160, fill=_shade(spec["skin"], -0.35), width=6 * s)

    # mouth
    my = eye_y + 84 * s
    if expression == "happy":
        d.arc([cx - 52 * s, my - 44 * s, cx + 52 * s, my + 26 * s], 20, 160, fill=INK, width=8 * s)
    elif expression in ("sad", "scared"):
        d.arc([cx - 44 * s, my + 6 * s, cx + 44 * s, my + 70 * s], 200, 340, fill=INK, width=8 * s)
    elif expression == "surprised":
        ellipse([cx - 22 * s, my - 22 * s, cx + 22 * s, my + 28 * s], (122, 43, 52), width=6 * s)
    else:
        d.line([cx - 40 * s, my, cx + 40 * s, my], fill=INK, width=8 * s)

    # blush
    for side in (-1, 1):
        d.ellipse([cx + side * 82 * s - 34 * s, eye_y + 26 * s, cx + side * 82 * s + 34 * s, eye_y + 66 * s],
                  fill=(230, 110, 120, 60))

    img = img.resize((W, H), Image.LANCZOS)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    img.save(out_path)
    return out_path


ENV_PALETTES = {
    "town": ("#7ec8f0", "#8fae6e", "#e8d5b7"),
    "bridge": ("#9ec6e8", "#3f7fa8", "#a89a86"),
    "ship": ("#2f6f96", "#2f6f96", "#7a4a2a"),
    "castle": ("#7ec8f0", "#6f9c56", "#b9b0a4"),
    "desert": ("#ffd9a0", "#e0b878", "#d8ad6c"),
    "lab": ("#3a3f52", "#6f7785", "#9aa4b2"),
    "palace": ("#9ec6e8", "#e6dcc4", "#f2ecdd"),
    "mountains": ("#7ec8f0", "#7aa05a", "#8b8f9c"),
    "house": ("#7ec8f0", "#7fae5e", "#f0ddc2"),
    "road": ("#9ec6e8", "#6f9c56", "#4e4e57"),
    "trees": ("#7ec8f0", "#5f8f4c", "#4f8f4a"),
    "water": ("#7ec8f0", "#3f8fb8", "#9a9086"),
    "clouds": ("#cfe8ff", "#cfe8ff", "#ffffff"),
    "plain": ("#7ec8f0", "#79a75c", "#a89a86"),
}


def draw_environment_card(name: str, out_path: str, size=(1080, 1920)) -> str:
    """Flat painted backdrop — used when the 3D environment is not wanted."""
    top, ground, accent = ENV_PALETTES.get(name, ENV_PALETTES["plain"])
    w, h = size
    img = Image.new("RGB", (w, h), _rgb(top))
    d = ImageDraw.Draw(img)

    horizon = int(h * 0.62)
    for y in range(horizon):
        f = y / max(1, horizon)
        d.line([(0, y), (w, y)], fill=tuple(int(a + (b - a) * f) for a, b in zip(_rgb(top), _rgb("#ffe6c0"))))

    d.rectangle([0, horizon, w, h], fill=_rgb(ground))

    # simple painted blobs on the ground
    hsh = hashlib.sha256(name.encode()).digest()
    for i in range(60):
        x = (hsh[i % len(hsh)] * 37 + i * 61) % w
        y = horizon + ((hsh[(i + 1) % len(hsh)] * 53 + i * 97) % (h - horizon))
        r = 30 + (hsh[(i + 2) % len(hsh)] % 70)
        d.ellipse([x - r, y - r // 3, x + r, y + r // 3], fill=_shade(ground, -0.08 if i % 2 else 0.06))

    # a few silhouettes so the plate is not empty
    for i in range(5):
        bx = 120 + i * 200
        bh = 180 + (hsh[i] % 160)
        d.rectangle([bx, horizon - bh, bx + 140, horizon], fill=_shade(accent, -0.05), outline=INK, width=6)
        d.polygon([(bx - 20, horizon - bh), (bx + 70, horizon - bh - 90), (bx + 160, horizon - bh)],
                  fill=_shade(accent, -0.25), outline=INK)

    img = img.filter(ImageFilter.SMOOTH)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    img.save(out_path)
    return out_path


def generate_all(story_path: str, out_dir: str) -> list:
    """Build every card the story needs. Returns the list of written files."""
    written = []
    story = {}
    if story_path and os.path.exists(story_path):
        with open(story_path, "r", encoding="utf-8") as f:
            story = json.load(f)

    characters = story.get("characters") or [{"id": "hero", "name": "Hero"}]
    for ch in characters:
        cid = ch.get("id", "hero")
        path = os.path.join(out_dir, f"char_{cid}.png")
        written.append(draw_character_card(character_spec(cid, ch), path))

    return written


def main() -> int:
    ap = argparse.ArgumentParser(description="Procedural fallback art (no network)")
    ap.add_argument("--story", default="work/story.json")
    ap.add_argument("--out", default="assets/generated")
    ap.add_argument("--env", default=None, help="also render one environment card")
    ap.add_argument("--char", default=None, help="render a single character card by id")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)

    if args.char:
        path = draw_character_card(character_spec(args.char), os.path.join(args.out, f"char_{args.char}.png"))
        print(f"[fallback] {path}")
        return 0

    files = generate_all(args.story, args.out)
    for f in files:
        print(f"[fallback] {f}")

    if args.env:
        path = draw_environment_card(args.env, os.path.join(args.out, f"env_{args.env}.png"))
        print(f"[fallback] {path}")

    print(f"[fallback] {len(files)} character cards written to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
