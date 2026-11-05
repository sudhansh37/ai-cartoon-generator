// motion.js — all interpolation, easing, camera choreography and character
// animation curves. Every function is pure: same (shot, progress, time) always
// produces the same pose, so a frame can be re-rendered identically after an
// interrupted run.

export const clamp = (v, a = 0, b = 1) => Math.min(b, Math.max(a, v));
export const lerp = (a, b, t) => a + (b - a) * t;
export const lerp3 = (a, b, t) => [lerp(a[0], b[0], t), lerp(a[1], b[1], t), lerp(a[2], b[2], t)];

export const EASINGS = {
  linear: (t) => t,
  easeIn: (t) => t * t * t,
  easeOut: (t) => 1 - Math.pow(1 - t, 3),
  easeInOut: (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2),
  smooth: (t) => t * t * (3 - 2 * t),
  backOut: (t) => 1 + 2.2 * Math.pow(t - 1, 3) + 1.2 * Math.pow(t - 1, 2),
  elasticOut: (t) => (t === 0 || t === 1 ? t : Math.pow(2, -9 * t) * Math.sin((t * 10 - 0.75) * ((2 * Math.PI) / 3)) + 1),
  step: (t) => (t < 0.5 ? 0 : 1),
};

export function ease(name, t) {
  return (EASINGS[name] || EASINGS.easeInOut)(clamp(t));
}

/* Deterministic value noise, used for handheld shake and drift. */
function hash1(n) {
  const s = Math.sin(n * 127.1) * 43758.5453;
  return s - Math.floor(s);
}
export function noise1(x) {
  const i = Math.floor(x);
  const f = x - i;
  const u = f * f * (3 - 2 * f);
  return lerp(hash1(i), hash1(i + 1), u) * 2 - 1;
}

/* --------------------------- camera choreography ---------------------------
 * A shot's camera is defined as {move, from:[x,y,z], to:[x,y,z], lookAt:[..],
 * lookAtTo:[..], fov}. Anything missing is derived from the move name, so a
 * planner can be lazy and still get a cinematic move.
 * ------------------------------------------------------------------------- */
const MOVE_PRESETS = {
  static: { from: [0, 1.7, 7.0], to: [0, 1.7, 7.0], lookAt: [0, 1.3, 0] },
  'push-in': { from: [0, 1.7, 8.0], to: [0, 1.55, 4.2], lookAt: [0, 1.35, 0] },
  'pull-out': { from: [0, 1.6, 4.0], to: [0, 2.0, 9.5], lookAt: [0, 1.3, 0] },
  'pan-left': { from: [3.2, 1.7, 6.0], to: [-3.2, 1.7, 6.0], lookAt: [0, 1.3, 0] },
  'pan-right': { from: [-3.2, 1.7, 6.0], to: [3.2, 1.7, 6.0], lookAt: [0, 1.3, 0] },
  'crane-up': { from: [0, 0.8, 6.5], to: [0, 3.6, 6.0], lookAt: [0, 1.4, 0] },
  'crane-down': { from: [0, 3.8, 6.5], to: [0, 1.1, 5.5], lookAt: [0, 1.2, 0] },
  orbit: { from: [4.5, 1.8, 4.5], to: [-4.5, 1.8, 4.5], lookAt: [0, 1.3, 0] },
  tracking: { from: [-2.6, 1.6, 5.4], to: [2.6, 1.6, 5.4], lookAt: [0, 1.3, 0] },
  reveal: { from: [0, 2.6, 9.5], to: [0, 1.5, 5.0], lookAt: [0, 1.5, 0] },
  'tilt-up': { from: [0, 0.9, 5.0], to: [0, 0.9, 5.0], lookAt: [0, 0.9, 0], lookAtTo: [0, 3.0, 0] },
  'dolly-zoom': { from: [0, 1.7, 7.5], to: [0, 1.6, 3.6], lookAt: [0, 1.4, 0], fov: 44, fovTo: 26 },
};

export const CAMERA_MOVES = Object.keys(MOVE_PRESETS);

/* Shot size controls how far the camera sits from its subject. Without this,
 * a "wide" and an "extreme close-up" would be framed identically, because the
 * camera presets only describe the move. */
export const SHOT_SIZE_SCALE = {
  wide: 1.15,
  environment: 1.30,
  medium: 1.00,
  'close-up': 0.78,
  'extreme-close-up': 0.62,
  reaction: 0.68,
};

// A camera closer than this to its subject ends up inside fences, walls and
// props, so the distance is clamped from below.
export const MIN_SUBJECT_DISTANCE = 4.6;

const HEAD_SHOTS = new Set(['close-up', 'extreme-close-up', 'reaction']);

export function shotCamera(shot, p, t) {
  const cam = shot.camera || {};
  const preset = MOVE_PRESETS[cam.move] || MOVE_PRESETS.static;
  const from = cam.from || preset.from;
  const to = cam.to || preset.to;
  const lookFrom = cam.lookAt || preset.lookAt;
  const lookTo = cam.lookAtTo || preset.lookAtTo || lookFrom;
  const fovFrom = cam.fov != null ? cam.fov : preset.fov != null ? preset.fov : 40;
  const fovTo = cam.fovTo != null ? cam.fovTo : preset.fovTo != null ? preset.fovTo : fovFrom;

  const e = ease(cam.easing || 'easeInOut', p);
  let pos = lerp3(from, to, e);
  const look = lerp3(lookFrom, lookTo, e);

  // frame the shot: distance scales with shot size, close-ups aim at the head
  const k = cam.scale != null ? cam.scale : (SHOT_SIZE_SCALE[shot.shotSize] || 1);
  pos = [
    look[0] + (pos[0] - look[0]) * k,
    look[1] + (pos[1] - look[1]) * (0.6 + 0.4 * k),
    look[2] + (pos[2] - look[2]) * k,
  ];
  if (HEAD_SHOTS.has(shot.shotSize)) {
    look[1] = look[1] + (1.75 - look[1]) * 0.75;
  }

  // keep the camera outside the set dressing
  const dx = pos[0] - look[0];
  const dz = pos[2] - look[2];
  const flat = Math.hypot(dx, dz);
  if (flat > 1e-4 && flat < MIN_SUBJECT_DISTANCE) {
    const f = MIN_SUBJECT_DISTANCE / flat;
    pos = [look[0] + dx * f, pos[1], look[2] + dz * f];
  }

  // never let the lens sink to floor level — a low angle turns the ground
  // plane into the whole frame
  pos[1] = Math.max(pos[1], 0.95);

  // handheld drift keeps every shot alive (never a frozen frame)
  const amp = cam.handheld != null ? cam.handheld : 0.035;

  pos = [
    pos[0] + noise1(t * 0.9) * amp,
    pos[1] + noise1(t * 1.1 + 10) * amp,
    pos[2] + noise1(t * 0.7 + 20) * amp * 0.5,
  ];

  return { pos, look, fov: lerp(fovFrom, fovTo, e) };
}

/* --------------------------- character animation ---------------------------
 * shotChar = {id, pose, expression, x, z, facing, action, phaseOffset}
 * ------------------------------------------------------------------------- */
export function blinkAmount(t, offset = 0) {
  // one blink every ~3.4s, lasting ~0.13s
  const period = 3.4;
  const local = ((t + offset) % period) / period;
  const start = 0.62;
  const dur = 0.04;
  if (local < start || local > start + dur) return 0;
  const k = (local - start) / dur;
  return Math.sin(k * Math.PI);
}

const POSE_PHASE_SPEED = { walk: 5.2, run: 8.4 };

export function characterSample(shot, shotChar, p, t, plan) {
  const id = shotChar.id;
  const base = (plan && plan.characters ? plan.characters.find((c) => c.id === id) : null) || {};
  const pose = shotChar.pose || base.defaultPose || 'stand';
  const expression = shotChar.expression || base.defaultExpression || 'neutral';

  // motion within the shot: an `enter` walk moves the character into frame
  const action = shotChar.action || 'idle';
  let x = shotChar.x != null ? shotChar.x : 0;
  let z = shotChar.z != null ? shotChar.z : 0;
  let facing = shotChar.facing != null ? shotChar.facing : 1;

  if (action === 'walk-in') {
    const from = shotChar.fromX != null ? shotChar.fromX : x - 3.2 * facing;
    x = lerp(from, x, ease('easeOut', Math.min(1, p * 1.6)));
  } else if (action === 'walk-out') {
    const to = shotChar.toX != null ? shotChar.toX : x + 3.2 * facing;
    x = lerp(x, to, ease('easeIn', clamp((p - 0.35) / 0.65)));
  } else if (action === 'drift') {
    x = x + Math.sin(t * 0.8) * 0.06;
  } else if (action === 'pop') {
    // impact reaction: a quick vertical pop
    z = z + Math.sin(clamp(p * 3) * Math.PI) * 0.15;
  }

  const phaseSpeed = POSE_PHASE_SPEED[pose] || 2.2;
  const phase = t * phaseSpeed + (shotChar.phaseOffset || 0);

  return {
    x,
    y: shotChar.y != null ? shotChar.y : 0,
    z,
    facing,
    pose,
    expression,
    phase,
    blink: blinkAmount(t, shotChar.blinkOffset || 0),
    mouthOpen: mouthOf(t, shotChar),
    headTurn: Math.sin(t * 0.7) * 0.5,
    lookX: Math.sin(t * 0.55) * 0.6,
    t,
  };
}

/* Mouth movement is driven by the phrase being spoken, when timing data
 * exists; otherwise a gentle procedural flap. */
export function mouthOf(t, shotChar = {}) {
  if (shotChar.talking === false) return 0;
  const rate = 7.5;
  const a = Math.sin(t * rate) * 0.5 + 0.5;
  const b = Math.sin(t * rate * 1.7 + 1.3) * 0.5 + 0.5;
  return clamp((a * 0.65 + b * 0.35) * 0.9);
}

/* Environment secondary motion curves. */
export function swayAmount(t, i = 0) {
  return Math.sin(t * 1.4 + i * 1.7) * 0.5 + Math.sin(t * 2.3 + i) * 0.25;
}
