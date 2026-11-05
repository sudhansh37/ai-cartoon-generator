// paint.js — procedural painted textures + toon shading helpers.
// Everything here is generated at runtime on a 2D canvas: no image files,
// no downloads. Gives the "painted 3D environment with black ink outlines"
// look described in the project spec.

import * as THREE from 'three';

/* ------------------------------------------------------------------ *
 * Deterministic RNG (never use Math.random for anything that ends up
 * in a rendered frame — frames must be reproducible for resume).
 * ------------------------------------------------------------------ */
export function mulberry32(seed) {
  let a = seed >>> 0;
  return function () {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export function makeCanvas(w, h) {
  const c = document.createElement('canvas');
  c.width = w;
  c.height = h;
  return c;
}

function hexToRgb(hex) {
  const h = hex.replace('#', '');
  const v = h.length === 3
    ? h.split('').map((c) => parseInt(c + c, 16))
    : [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
  return { r: v[0], g: v[1], b: v[2] };
}

export function shade(hex, amount) {
  const { r, g, b } = hexToRgb(hex);
  const f = (x) => Math.max(0, Math.min(255, Math.round(amount >= 0 ? x + (255 - x) * amount : x * (1 + amount))));
  return `rgb(${f(r)},${f(g)},${f(b)})`;
}

export function rgba(hex, alpha) {
  const { r, g, b } = hexToRgb(hex);
  return `rgba(${r},${g},${b},${alpha})`;
}

/* ------------------------------------------------------------------ *
 * Toon ramp: a 4-band gradient map shared by every MeshToonMaterial.
 * ------------------------------------------------------------------ */
let _ramp = null;
export function toonRamp(steps = 4) {
  if (_ramp) return _ramp;
  const data = new Uint8Array(steps);
  for (let i = 0; i < steps; i++) data[i] = Math.round((i / (steps - 1)) * 255);
  const tex = new THREE.DataTexture(data, steps, 1, THREE.RedFormat);
  tex.minFilter = THREE.NearestFilter;
  tex.magFilter = THREE.NearestFilter;
  tex.needsUpdate = true;
  _ramp = tex;
  return tex;
}

/* ------------------------------------------------------------------ *
 * Painted texture: flat base colour + soft paint blobs + brush strokes
 * + fine grain. `kind` only changes the stroke direction / blotch size.
 * ------------------------------------------------------------------ */
const KIND_PARAMS = {
  plaster: { blobs: 90, blobR: [12, 46], stroke: 0.35, grain: 0.07 },
  stone: { blobs: 70, blobR: [8, 30], stroke: 0.55, grain: 0.09 },
  wood: { blobs: 60, blobR: [4, 14], stroke: 0.85, grain: 0.06, streaks: true },
  sand: { blobs: 110, blobR: [10, 40], stroke: 0.2, grain: 0.05 },
  water: { blobs: 80, blobR: [20, 70], stroke: 0.25, grain: 0.03 },
  grass: { blobs: 140, blobR: [4, 18], stroke: 0.7, grain: 0.08 },
  metal: { blobs: 40, blobR: [6, 24], stroke: 0.5, grain: 0.04 },
  fabric: { blobs: 70, blobR: [10, 34], stroke: 0.6, grain: 0.06 },
  sky: { blobs: 40, blobR: [40, 120], stroke: 0.15, grain: 0.02 },
  paper: { blobs: 50, blobR: [8, 28], stroke: 0.4, grain: 0.07 },
};

const textureCache = new Map();

export function paintedTexture(kind = 'plaster', color = '#9c7b62', { size = 512, seed = 1, repeat = 1 } = {}) {
  const key = `${kind}|${color}|${size}|${seed}|${repeat}`;
  if (textureCache.has(key)) return textureCache.get(key);

  const p = KIND_PARAMS[kind] || KIND_PARAMS.plaster;
  const rnd = mulberry32(seed * 2654435761 + 12345);
  const c = makeCanvas(size, size);
  const ctx = c.getContext('2d');

  ctx.fillStyle = color;
  ctx.fillRect(0, 0, size, size);

  // soft painted blotches
  for (let i = 0; i < p.blobs; i++) {
    const x = rnd() * size;
    const y = rnd() * size;
    const r = p.blobR[0] + rnd() * (p.blobR[1] - p.blobR[0]);
    const light = rnd() > 0.5;
    const g = ctx.createRadialGradient(x, y, 0, x, y, r);
    g.addColorStop(0, rgba(light ? '#ffffff' : '#000000', 0.05 + rnd() * 0.09));
    g.addColorStop(1, rgba(light ? '#ffffff' : '#000000', 0));
    ctx.fillStyle = g;
    ctx.beginPath();
    ctx.arc(x, y, r, 0, Math.PI * 2);
    ctx.fill();
  }

  // brush strokes (horizontal, or vertical streaks for wood)
  const strokes = Math.round(size / 3);
  for (let i = 0; i < strokes * p.stroke; i++) {
    const light = rnd() > 0.5;
    ctx.strokeStyle = rgba(light ? '#ffffff' : '#000000', 0.02 + rnd() * 0.05);
    ctx.lineWidth = 1 + rnd() * 2.4;
    ctx.beginPath();
    const y = rnd() * size;
    const x = rnd() * size;
    const len = 20 + rnd() * size * 0.5;
    if (p.streaks) {
      ctx.moveTo(x, y);
      ctx.lineTo(x + (rnd() - 0.5) * 4, y + len);
    } else {
      ctx.moveTo(x, y);
      ctx.lineTo(x + len, y + (rnd() - 0.5) * 6);
    }
    ctx.stroke();
  }

  // fine grain
  const grain = Math.round(size * size * p.grain * 0.02);
  for (let i = 0; i < grain; i++) {
    const light = rnd() > 0.5;
    ctx.fillStyle = rgba(light ? '#ffffff' : '#000000', 0.05);
    ctx.fillRect(rnd() * size, rnd() * size, 1.5, 1.5);
  }

  const tex = new THREE.CanvasTexture(c);
  tex.wrapS = tex.wrapT = THREE.RepeatWrapping;
  tex.repeat.set(repeat, repeat);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 4;
  textureCache.set(key, tex);
  return tex;
}

/* Painted, toon-shaded material. */
export function paintedMaterial(kind, color, { seed = 1, repeat = 1, emissive = '#000000', side = THREE.FrontSide } = {}) {
  // NOTE: MeshToonMaterial has no flatShading flag — passing one only logs a
  // warning on every material, so it is deliberately not forwarded.
  return new THREE.MeshToonMaterial({
    map: paintedTexture(kind, color, { seed, repeat }),
    gradientMap: toonRamp(),
    color: new THREE.Color('#ffffff'),
    emissive: new THREE.Color(emissive),
    side,
  });
}

export function flatMaterial(color, { emissive = '#000000', side = THREE.FrontSide } = {}) {
  return new THREE.MeshToonMaterial({
    color: new THREE.Color(color),
    gradientMap: toonRamp(),
    emissive: new THREE.Color(emissive),
    side,
  });
}

/* Vertical sky gradient used as scene.background. */
export function skyTexture(top = '#7ec8f0', bottom = '#ffe6c0') {
  const c = makeCanvas(4, 256);
  const ctx = c.getContext('2d');
  const g = ctx.createLinearGradient(0, 0, 0, 256);
  g.addColorStop(0, top);
  g.addColorStop(1, bottom);
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, 4, 256);
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}

/* Black-ink outline helper: edges of the geometry as a plum line. */
export function addInk(mesh, { color = '#2b1730', opacity = 1, threshold = 35 } = {}) {
  const geo = new THREE.EdgesGeometry(mesh.geometry, threshold);
  const line = new THREE.LineSegments(
    geo,
    new THREE.LineBasicMaterial({ color: new THREE.Color(color), transparent: opacity < 1, opacity })
  );
  line.name = 'ink';
  line.renderOrder = 2;
  mesh.add(line);
  return line;
}

/* A soft radial "painted" blob texture, used for dust / impact / smoke. */
export function softDiscTexture(color = '#ffffff', softness = 0.55) {
  const size = 128;
  const c = makeCanvas(size, size);
  const ctx = c.getContext('2d');
  const g = ctx.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
  g.addColorStop(0, rgba(color, 1));
  g.addColorStop(softness, rgba(color, 0.45));
  g.addColorStop(1, rgba(color, 0));
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, size, size);
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}
