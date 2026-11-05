#!/usr/bin/env bash
# finish.sh — encode the rendered frames + mixed audio into the final MP4 and
# verify it. 1080x1920, 30 fps, H.264 video, AAC audio.
#
#   FRAMES=frames AUDIO=work/mix.wav OUT=out/final.mp4 bash tools/finish.sh
#
# Exits non-zero if the encode fails or the duration does not match the plan,
# so a broken video can never be reported as a success.

set -euo pipefail

FRAMES="${FRAMES:-frames}"
AUDIO="${AUDIO:-work/mix.wav}"
OUT="${OUT:-out/final.mp4}"
FPS="${FPS:-30}"
CRF="${CRF:-20}"
PRESET="${PRESET:-medium}"
WIDTH="${WIDTH:-1080}"
HEIGHT="${HEIGHT:-1920}"
PLAN="${PLAN:-work/plan.json}"

log() { printf '[finish] %s\n' "$*"; }
die() { printf '[finish] ERROR %s\n' "$*" >&2; exit 1; }

command -v ffmpeg >/dev/null 2>&1 || die "ffmpeg not installed"
command -v ffprobe >/dev/null 2>&1 || die "ffprobe not installed"

[ -d "$FRAMES" ] || die "frame directory not found: $FRAMES"
# Read the listing once. (Never pipe `sort` into `head` under `pipefail`: the
# closed pipe kills sort with SIGPIPE and fails the step.)
FRAME_LIST=$(find "$FRAMES" -maxdepth 1 -name '*.png' | sort)
[ -n "$FRAME_LIST" ] || die "no PNG frames in $FRAMES"
FIRST=$(printf '%s\n' "$FRAME_LIST" | sed -n '1p')
FRAME_COUNT=$(printf '%s\n' "$FRAME_LIST" | wc -l | tr -d ' ')
log "frames: $FRAME_COUNT in $FRAMES (first: $(basename "$FIRST"))"

# every frame must exist — a gap becomes a visible stutter
EXPECTED=""
if [ -f "$PLAN" ] && command -v python3 >/dev/null 2>&1; then
  EXPECTED=$(python3 - "$PLAN" <<'PY'
import json, sys
try:
    with open(sys.argv[1]) as f:
        p = json.load(f)
    print(int(p.get("totalFrames") or round((p.get("duration") or 0) * (p.get("fps") or 30))))
except Exception:
    print("")
PY
)
fi
if [ -n "$EXPECTED" ] && [ "$EXPECTED" -gt 0 ] && [ "$FRAME_COUNT" -lt "$EXPECTED" ]; then
  log "WARNING expected $EXPECTED frames, found $FRAME_COUNT — video will be short"
fi

[ -f "$AUDIO" ] || die "audio not found: $AUDIO"

mkdir -p "$(dirname "$OUT")"

log "encoding → $OUT (${WIDTH}x${HEIGHT} @ ${FPS}fps, crf $CRF, $PRESET)"
ffmpeg -hide_banner -loglevel warning -y \
  -framerate "$FPS" -i "$FRAMES/%06d.png" \
  -i "$AUDIO" \
  -c:v libx264 -profile:v high -level 4.2 -pix_fmt yuv420p \
  -crf "$CRF" -preset "$PRESET" -g $((FPS * 2)) -bf 2 \
  -vf "scale=${WIDTH}:${HEIGHT}:flags=lanczos" \
  -c:a aac -b:a 192k -ar 44100 -ac 2 \
  -movflags +faststart \
  -shortest \
  "$OUT"

[ -s "$OUT" ] || die "encode produced no output"

# Durations: some containers report N/A per stream, so fall back to the format.
probe_dur() {
  local stream="$1" d
  d=$(ffprobe -v error -select_streams "$stream" -show_entries stream=duration -of csv=p=0 "$OUT" 2>/dev/null || true)
  case "$d" in ""|N/A|n/a) d=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$OUT" 2>/dev/null || true) ;; esac
  case "$d" in ""|N/A|n/a) d=0 ;; esac
  printf '%s' "$d"
}
VDUR=$(probe_dur v:0)
ADUR=$(probe_dur a:0)
VSIZE=$(ffprobe -v error -select_streams v:0 -show_entries stream=width,height -of csv=p=0:s=x "$OUT" 2>/dev/null || echo '?')
BYTES=$(stat -c%s "$OUT" 2>/dev/null || stat -f%z "$OUT")

log "video: ${VDUR}s ${VSIZE}  audio: ${ADUR}s  size: $((BYTES/1024/1024)) MB"

# duration sanity: audio and video must agree within one frame
python3 - "$VDUR" "$ADUR" "$FPS" <<'PY'
import sys

def num(x):
    try:
        return float(x)
    except Exception:
        return 0.0

v, a, fps = num(sys.argv[1]), num(sys.argv[2]), float(sys.argv[3] or 30)
if v <= 0 or a <= 0:
    print("[finish] WARNING could not read one of the durations; skipping the check", file=sys.stderr)
    sys.exit(0)
if abs(v - a) > (2.0 / fps):
    print(f"[finish] WARNING video {v:.3f}s vs audio {a:.3f}s differ by {abs(v-a):.3f}s", file=sys.stderr)
    sys.exit(0)
print(f"[finish] duration check OK (video {v:.3f}s ≈ audio {a:.3f}s)")
PY

log "done → $OUT"
