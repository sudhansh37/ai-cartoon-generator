# AI Cartoon Audio Shorts Generator

Turn a voiceover (or a script) into a **1080 × 1920, 30 fps cartoon short** with
flat 2D bean-head characters acting inside painted 3D worlds — fully automated,
entirely on GitHub Actions, driven from your phone. No local PC, no GPU.

```
ideas (Gemini) → script → voiceover → timing → shot plan → frames (Three.js) → music + SFX → MP4
```

Everything runs on the GitHub Actions CPU runner. The finished video comes back
as a **downloadable artifact** — nothing is committed to the repository.

---

## Start here (from your phone)

1. **Add your key** — *Settings → Secrets and variables → Actions → New repository secret*
   - `GEMINI_API_KEY` — your Google AI Studio key (required for story/script/plan)
   - `POLLINATIONS_API_KEY` — optional, only if you want AI-generated image plates
2. **Tab → Actions → “1 · Generate story ideas” → Run workflow.**
   Pick a theme, run it, then download the `story-ideas` artifact and pick a number.
3. **Tab → Actions → “2 · Generate video” → Run workflow.**

| input | what to put |
|---|---|
| `mode` | `voiceover-ready` if you already recorded; `script-ready` if you only have the script; `ideas-needed` to let it write the story |
| `script_text` | your final narration (required unless `mode = ideas-needed`) |
| `voiceover_url` | direct download link to your MP3/WAV (Drive/Dropbox “direct download” link) |
| `idea_index` | which of the 10 ideas to build |
| `duration_sec` | 20–30 by default, 45–60 also supported |
| `preview_only` | `true` renders 8 stills + a contact sheet first — cheap way to check framing |

When the run turns green, open the run page → **Artifacts** → `cartoon-short`.
That zip has `final.mp4`, the contact sheet, `PLAN.md` and the plan JSON.

> Tip: always do one `preview_only` run before a full render. A 26-second short
> is ~780 frames; on a standard runner that is roughly 20–40 minutes of CPU
> WebGL, so check the stills first.

---

## How it works

| stage | file | what it does |
|---|---|---|
| Story ideas | `ai/ideas.py` | exactly 10 original ideas: title, hook, twist, why it animates, `true`/`disputed`/`folklore` |
| Script | `ai/script.py` | narration (20–30 s default, 45–60 s allowed) + a character sheet (1–3 characters) |
| Voiceover | `ai/tts.py` | optional Gemini TTS when you have no recording |
| Timing | `tools/timing.py` | speech detection + phrase/word alignment → `phrases.json`, frame boundaries |
| Shot plan | `ai/scene_planner.py` | one shot per sentence → `plan.json` + human-readable `PLAN.md` |
| Characters | `web/toon.js` | the bean-head drawing system: faces, hair, outfits, poses, expressions, blinking |
| World | `web/main.js` | Three.js environments from primitives + procedural painted textures + ink outlines |
| Motion | `web/motion.js` | easings, camera moves, idle breathing, walk/run cycles, handheld drift |
| Frames | `render.mjs` | Playwright + Chromium + SwiftShader, frame-by-frame, resumable |
| Music/SFX | `audio/audio.py` | procedural march loop (kick, snare, plucked bass, brass pad, drone) + 9 SFX |
| Mix | `audio/mix.py` | voice untouched, music at −6 dB, SFX capped at 0.7 × voice peak |
| Encode | `tools/finish.sh` | ffmpeg → H.264 + AAC, faststart, duration verified |

### Visual system

- **Characters** are 2D cards drawn on a canvas each frame and billboarded inside
  the 3D scene. Same character spec ⇒ same face, hair and outfit in every shot,
  so identity never drifts.
- **Environments** (`town`, `bridge`, `ship`, `castle`, `desert`, `lab`, `palace`,
  `mountains`, `house`, `road`, `trees`, `water`, `clouds`, `plain`) are built from
  primitives with painted textures, plum ink outlines, toon shading, shadows, fog
  and depth of field.
- **Motion** never freezes: idle breathing, blinking, head turns, walk/run cycles,
  arm gestures, particles, water drift, flags and clouds.

### Timing rules

The video follows the voiceover, never the other way round. `tools/timing.py`
detects speech regions, aligns each sentence to them, and hands the renderer exact
frame boundaries. The voice file is decoded to WAV and summed in unchanged — it is
never retimed, pitch-shifted or re-encoded by this project.

### Audio rules

- music = voice level − 6 dB
- SFX = voice level − 6 dB, peaks hard-capped at 0.7 × the voice peak
- a limiter only ever touches the *summed* mix, never the voice alone

---

## Repository layout

```
ai-cartoon-generator/
├── .github/workflows/
│   ├── generate-ideas.yml      # run manually → 10 ideas artifact
│   └── generate-video.yml      # run manually → final MP4 artifact
├── ai/
│   ├── gemini.py               # REST client, model fallback, retries
│   ├── ideas.py                # 10 story ideas
│   ├── script.py               # narration + character sheet
│   ├── scene_planner.py        # plan.json + PLAN.md
│   └── tts.py                  # optional Gemini TTS
├── imagegen/
│   ├── pollinations.py         # optional AI images (live catalog, safe retries)
│   ├── downloader.py           # resumable downloader with the retry policy
│   └── fallback.py             # Pillow character cards when AI images are down
├── web/
│   ├── index.html              # render stage (importmap → node_modules/three)
│   ├── main.js                 # scene assembly + per-frame render
│   ├── toon.js                 # bean-head character drawing system
│   ├── motion.js               # easings, camera + character animation
│   ├── paint.js                # procedural painted textures, ink outlines
│   └── story.js                # plan schema + validation
├── audio/{audio.py,mix.py}
├── tools/{timing.py,sheet.py,finish.sh}
├── assets/                     # your inputs + assets/sample/
├── work/ frames/ out/          # generated, git-ignored
├── render.mjs
├── config.json                 # all tunables in one place
├── requirements.txt
├── package.json
└── README.md
```

---

## Run it locally (optional)

```bash
# one-time
npm install
npx playwright install --with-deps chromium
python -m pip install -r requirements.txt

# render the bundled sample (4 shots, 180 frames, no voiceover needed)
node render.mjs --plan assets/sample/plan.json --out frames
python tools/sheet.py --frames frames --out work/contact_sheet.png

# quick look: 8 stills instead of 180 frames
node render.mjs --plan assets/sample/plan.json --stills 8 --stills-dir work/stills

# full pipeline by hand
python tools/timing.py --voice assets/voice.wav --script work/script.txt --out work/phrases.json
python ai/scene_planner.py --script work/script.txt --phrases work/phrases.json \
    --story work/story.json --out work/plan.json --plan-md PLAN.md
python audio/audio.py --plan work/plan.json --out-dir work
python audio/mix.py --voice assets/voice.wav --out work/mix.wav
FRAMES=frames AUDIO=work/mix.wav OUT=out/final.mp4 bash tools/finish.sh
```

`render.mjs` flags: `--plan --out --range a-b --stills n --no-resume --force --serve --quality --log-every`.
Interrupted? Just run it again — existing frames are skipped and it resumes.

### Configuration

`config.json` holds the video size, fps, CRF, AI model list, image settings and
the audio gain rules. The Gemini model is chosen in this order:
`GEMINI_MODEL` (env / repository variable) → the list in `config.json`.
If your key does not have a model, the client walks the list and then fails with a
clear message instead of guessing.

---

## Optional AI images

`imagegen/pollinations.py` targets the current Pollinations API
(`https://gen.pollinations.ai/image/{prompt}?model=…`, bearer-key auth), reads the
live model catalog and resolves aliases before generating, so no dead endpoint is
hardcoded — change `POLLINATIONS_BASE_URL` / `POLLINATIONS_MODEL` if the service
moves. Retry policy is deliberate: `502/503/504` → exponential backoff, `429` →
`Retry-After`, `401/403` → stop immediately, `402` → report insufficient balance,
capped attempts, no IP rotation.

**It is never required.** If generation is unavailable the pipeline keeps going
with `imagegen/fallback.py` (Pillow character cards) and the Three.js environments.

---

## Costs and limits

- Public repo ⇒ GitHub-hosted standard runners are free, but jobs are still capped
  at 90 minutes here (`timeout-minutes: 90`). A 60-second short may need two runs —
  use `--range` locally or re-run (resume) if a job hits the ceiling.
- Rendering is CPU WebGL via SwiftShader. It is correct, not fast: budget roughly
  2–4 s per 1080×1920 frame on a standard runner.
- Generated media is never committed; artifacts expire after 14 days (7 for previews).

---

## Troubleshooting

| symptom | fix |
|---|---|
| `GEMINI_API_KEY is not set` | add the repository secret; the pipeline still runs with offline ideas/scripts |
| Gemini 400/404 on every model | set the repository **variable** `GEMINI_MODEL` to a model your key supports |
| `TTS unavailable` | upload your MP3/WAV and run in `voiceover-ready` mode |
| frames render but the video is short | a frame failed — check `work/render.log`, re-run (it resumes) |
| characters look identical | give each character a different `outfit`, `hair` and `headgear` in the plan |
| video has no sound | check `assets/voice.wav` exists and `work/mix.wav` was produced |
| job killed at 90 min | use `preview_only` first, then a shorter `duration_sec`, or split with `--range` |

---

## Honest status

- The animation engine, character system, planner, timing, audio synth, mix, encode
  script and both workflows are complete code — no placeholder functions.
- The offline paths (rule-based planner, offline ideas, deterministic scripts,
  Pillow fallback art, energy-based alignment) work with **no API key at all**.
- Things that depend on live third-party services (Gemini model availability,
  Gemini TTS access, Pollinations availability) can change on their side; each has
  a documented failure message and, where possible, a fallback.
- Nothing here claims a feature works until it has been run. The bundled sample
  plan in `assets/sample/` is the reference input for checking the renderer.

---

## Credits

Character architecture inspired by the toon.ts style of parametric bean-head
drawing; 3D staging built on [three.js](https://threejs.org); frame capture with
[Playwright](https://playwright.dev); encoding with FFmpeg.
