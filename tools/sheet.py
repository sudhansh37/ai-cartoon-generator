#!/usr/bin/env python3
"""sheet.py — stills contact sheet.

Pulls evenly spaced frames out of a rendered sequence and lays them out on one
PNG so a human can check framing, characters, expressions and lighting before
spending minutes on the full encode.

    python tools/sheet.py --frames frames --out work/contact_sheet.png --count 12
"""

from __future__ import annotations

import argparse
import glob
import os
import sys

from PIL import Image, ImageDraw

THUMB_W = 270
THUMB_H = 480
PAD = 10
LABEL_H = 22


def pick(files, count):
    if len(files) <= count:
        return files
    step = len(files) / float(count)
    return [files[min(len(files) - 1, int(i * step))] for i in range(count)]


def build_sheet(frames_dir: str, out_path: str, count: int = 12, cols: int = 4) -> str:
    files = sorted(glob.glob(os.path.join(frames_dir, "*.png")))
    if not files:
        raise FileNotFoundError(f"no PNG frames in {frames_dir}")
    chosen = pick(files, count)

    cols = max(1, min(cols, len(chosen)))
    rows = (len(chosen) + cols - 1) // cols
    sheet_w = cols * THUMB_W + (cols + 1) * PAD
    sheet_h = rows * (THUMB_H + LABEL_H) + (rows + 1) * PAD

    sheet = Image.new("RGB", (sheet_w, sheet_h), (18, 18, 26))
    draw = ImageDraw.Draw(sheet)

    for i, path in enumerate(chosen):
        r, c = divmod(i, cols)
        x = PAD + c * (THUMB_W + PAD)
        y = PAD + r * (THUMB_H + LABEL_H + PAD)
        try:
            img = Image.open(path).convert("RGB")
            img = img.resize((THUMB_W, THUMB_H), Image.LANCZOS)
            sheet.paste(img, (x, y))
        except Exception as exc:  # a broken frame must not kill the sheet
            draw.rectangle([x, y, x + THUMB_W, y + THUMB_H], outline=(200, 60, 60))
            draw.text((x + 8, y + 8), f"error: {exc}", fill=(255, 120, 120))
        label = os.path.basename(path)
        draw.text((x + 4, y + THUMB_H + 5), label, fill=(210, 214, 226))

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    sheet.save(out_path)
    return out_path


def main() -> int:
    ap = argparse.ArgumentParser(description="Contact sheet of rendered stills")
    ap.add_argument("--frames", default="frames", help="frame directory")
    ap.add_argument("--out", default="work/contact_sheet.png")
    ap.add_argument("--count", type=int, default=12)
    ap.add_argument("--cols", type=int, default=4)
    args = ap.parse_args()

    path = build_sheet(args.frames, args.out, args.count, args.cols)
    size = os.path.getsize(path)
    print(f"[sheet] wrote {path} ({size/1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
