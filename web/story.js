// story.js — plan (shot list) model shared by the renderer and the planner.
// Keeps the plan.json schema in one place and normalises anything the AI
// scene planner left out.

export const ENVIRONMENTS = [
  'town', 'bridge', 'ship', 'castle', 'desert', 'lab', 'palace',
  'mountains', 'house', 'road', 'trees', 'water', 'clouds', 'plain',
];

export const SHOT_SIZES = ['wide', 'medium', 'close-up', 'extreme-close-up', 'reaction', 'environment'];

export const LIGHTING = ['dawn', 'day', 'dusk', 'night', 'storm', 'interior', 'magic'];

export const POSES = ['stand', 'walk', 'run', 'sit', 'point', 'react', 'wave', 'carry'];

export const EXPRESSIONS = ['neutral', 'happy', 'sad', 'angry', 'surprised', 'scared', 'smug', 'determined'];

export const CAMERA_MOVES = [
  'static', 'push-in', 'pull-out', 'pan-left', 'pan-right', 'crane-up',
  'crane-down', 'orbit', 'tracking', 'reveal', 'tilt-up', 'dolly-zoom',
];

export const SFX = [
  'whoosh', 'thunk', 'crash', 'rumble', 'scrape', 'ting', 'creak', 'splash', 'ambience',
];

/* Distance the camera should sit at, by shot size — used when the planner
 * only names a shot size and no explicit camera path. */
export const SHOT_DISTANCE = {
  wide: 9.5,
  medium: 6.0,
  'close-up': 3.4,
  'extreme-close-up': 2.2,
  reaction: 3.0,
  environment: 11.0,
};

export function normalizePlan(raw) {
  const fps = raw.fps || 30;
  const plan = {
    title: raw.title || 'Untitled',
    fps,
    width: raw.width || 1080,
    height: raw.height || 1920,
    duration: raw.duration || 0,
    audio: raw.audio || {},
    style: raw.style || 'flat-2d-bean-head',
    render: raw.render || {},
    characters: (raw.characters || []).map((c, i) => ({
      id: c.id || `char${i}`,
      name: c.name || c.id || `Character ${i + 1}`,
      skin: c.skin || '#f0c49a',
      hair: c.hair || '#2a1a12',
      hairStyle: c.hairStyle || 'short',
      outfit: c.outfit || '#c0392b',
      outfitStyle: c.outfitStyle || 'tunic',
      pants: c.pants || '#2f3b52',
      shoe: c.shoe || '#3a2a1e',
      headgear: c.headgear || 'none',
      accessory: c.accessory || 'none',
      defaultPose: c.defaultPose || 'stand',
      defaultExpression: c.defaultExpression || 'neutral',
    })),
    shots: (raw.shots || []).map((s, i) => normalizeShot(s, i, fps)),
  };

  // make the timeline contiguous
  let cursor = 0;
  for (const s of plan.shots) {
    if (s.startFrame == null) s.startFrame = cursor;
    if (s.endFrame == null) s.endFrame = s.startFrame + Math.round((s.duration || 2) * fps);
    cursor = s.endFrame;
  }

  if (!plan.duration) {
    plan.duration = plan.shots.length ? plan.shots[plan.shots.length - 1].endFrame / fps : 0;
  }
  plan.totalFrames = raw.totalFrames || Math.max(1, Math.round(plan.duration * fps));
  return plan;
}

function normalizeShot(s, i, fps) {
  const start = s.startFrame != null ? s.startFrame : null;
  const end = s.endFrame != null ? s.endFrame : null;
  return {
    index: i,
    startFrame: start,
    endFrame: end,
    duration: s.duration || 2.0,
    words: s.words || s.text || '',
    text: s.text || s.words || '',
    shotSize: SHOT_SIZES.includes(s.shotSize) ? s.shotSize : 'medium',
    environment: ENVIRONMENTS.includes(s.environment) ? s.environment : 'town',
    environmentVariant: s.environmentVariant || s.variant || 1,
    lighting: LIGHTING.includes(s.lighting) ? s.lighting : 'day',
    camera: {
      move: CAMERA_MOVES.includes(s.camera && s.camera.move) ? s.camera.move : 'push-in',
      ...(s.camera || {}),
    },
    characters: (s.characters || []).map((c, j) => ({
      id: c.id || (c.name ? c.name : `char${j}`),
      pose: POSES.includes(c.pose) ? c.pose : 'stand',
      expression: EXPRESSIONS.includes(c.expression) ? c.expression : 'neutral',
      x: c.x != null ? c.x : 0,
      z: c.z != null ? c.z : 0,
      facing: c.facing != null ? c.facing : 1,
      action: c.action || 'idle',
      scale: c.scale || 1,
      talking: c.talking !== false,
      phaseOffset: c.phaseOffset || 0,
      blinkOffset: c.blinkOffset || 0,
    })),
    props: s.props || [],
    vfx: s.vfx || [],
    sfx: (s.sfx || []).map((x) => (typeof x === 'string' ? { at: 0.4, name: x, gain: 0.6 } : x)),
    music: s.music || null,
    transition: s.transition || 'cut',
  };
}

export function shotAt(plan, frame) {
  const shots = plan.shots;
  for (let i = 0; i < shots.length; i++) {
    if (frame >= shots[i].startFrame && frame < shots[i].endFrame) return shots[i];
  }
  return shots[shots.length - 1] || null;
}

export function shotProgress(shot, frame) {
  const span = Math.max(1, shot.endFrame - shot.startFrame);
  return Math.min(1, Math.max(0, (frame - shot.startFrame) / span));
}

/* Cheap structural validation — the renderer refuses to start on a broken plan
 * rather than producing a broken video. */
export function validatePlan(plan) {
  const problems = [];
  if (!plan.shots || !plan.shots.length) problems.push('plan has no shots');
  if (!plan.characters || !plan.characters.length) problems.push('plan has no characters');
  plan.shots.forEach((s, i) => {
    if (s.endFrame <= s.startFrame) problems.push(`shot ${i}: endFrame <= startFrame`);
    if (!s.words) problems.push(`shot ${i}: no spoken words (every shot must illustrate a sentence)`);
  });
  const last = plan.shots[plan.shots.length - 1];
  if (last && Math.abs(last.endFrame - plan.totalFrames) > 1) {
    problems.push(`timeline mismatch: last shot ends at ${last.endFrame}, plan has ${plan.totalFrames} frames`);
  }
  return problems;
}
