// main.js — the animation engine runtime.
//
// The renderer (render.mjs) loads this page once and then calls
// window.renderFrame(i) for every frame. Each call is a pure function of the
// frame index: build the shot's environment, place the character cards, run the
// animation curves for time t = i / fps, render. That makes frames reproducible
// and lets a crashed run resume from any frame.

import * as THREE from 'three';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { BokehPass } from 'three/addons/postprocessing/BokehPass.js';

import { paintedMaterial, flatMaterial, skyTexture, addInk, mulberry32, softDiscTexture } from './paint.js';
import { drawCharacter, CARD_W, CARD_H, characterFromSeed } from './toon.js';
import { shotAt, shotProgress, normalizePlan, validatePlan } from './story.js';
import { shotCamera, characterSample } from './motion.js';

/* ------------------------------------------------------------------ *
 * Global runtime state
 * ------------------------------------------------------------------ */
const R = {
  plan: null,
  scene: null,
  camera: null,
  renderer: null,
  composer: null,
  bokeh: null,
  width: 1080,
  height: 1920,
  fps: 30,
  envs: new Map(),      // "kind|variant" -> { group, updaters }
  charCards: new Map(), // character id -> { mesh, canvas, texture, ctx }
  activeShotIndex: -1,
  activeEnvKey: null,
  lighting: null,
  particles: [],
  postprocessing: true,
  inkOutlines: true,
};

/* ------------------------------------------------------------------ *
 * Small geometry helpers
 * ------------------------------------------------------------------ */
const ink = { color: '#2b1730', threshold: 35 };

function box(w, h, d, mat, x = 0, y = 0, z = 0, inkOn = true) {
  const m = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), mat);
  m.position.set(x, y, z);
  m.castShadow = true;
  m.receiveShadow = true;
  if (inkOn && R.inkOutlines) addInk(m, ink);
  return m;
}

function cyl(rt, rb, h, seg, mat, x = 0, y = 0, z = 0, inkOn = true) {
  const m = new THREE.Mesh(new THREE.CylinderGeometry(rt, rb, h, seg), mat);
  m.position.set(x, y, z);
  m.castShadow = true;
  m.receiveShadow = true;
  if (inkOn && R.inkOutlines) addInk(m, ink);
  return m;
}

function cone(r, h, seg, mat, x = 0, y = 0, z = 0, inkOn = true) {
  const m = new THREE.Mesh(new THREE.ConeGeometry(r, h, seg), mat);
  m.position.set(x, y, z);
  m.castShadow = true;
  if (inkOn && R.inkOutlines) addInk(m, ink);
  return m;
}

function sphere(r, mat, x = 0, y = 0, z = 0, inkOn = false) {
  const m = new THREE.Mesh(new THREE.SphereGeometry(r, 20, 14), mat);
  m.position.set(x, y, z);
  m.castShadow = true;
  m.receiveShadow = true;
  if (inkOn && R.inkOutlines) addInk(m, ink);
  return m;
}

function plane(w, h, mat, { x = 0, y = 0, z = 0, rx = 0, ry = 0, rz = 0 } = {}) {
  const m = new THREE.Mesh(new THREE.PlaneGeometry(w, h), mat);
  m.position.set(x, y, z);
  m.rotation.set(rx, ry, rz);
  m.receiveShadow = true;
  return m;
}

function ground(kind, color, size = 80, y = 0) {
  const mat = paintedMaterial(kind, color, { repeat: size / 8, seed: 7 });
  const m = plane(size, size, mat, { x: 0, y, z: 0, rx: -Math.PI / 2 });
  return m;
}

function tree(rng, x, z, s = 1) {
  const g = new THREE.Group();
  const trunk = cyl(0.14 * s, 0.2 * s, 1.4 * s, 8, paintedMaterial('wood', '#6b4a2f', { seed: 3 }), 0, 0.7 * s, 0);
  g.add(trunk);
  const leafCol = ['#4f8f4a', '#3f7f45', '#5fa055', '#2f6f3f'][Math.floor(rng() * 4)];
  for (let i = 0; i < 3; i++) {
    const r = (0.85 - i * 0.16) * s;
    const b = sphere(r, paintedMaterial('grass', leafCol, { seed: i + 5 }), 0, (1.5 + i * 0.55) * s, 0, true);
    g.add(b);
  }
  g.position.set(x, 0, z);
  g.rotation.y = rng() * Math.PI * 2;
  return g;
}

function house(rng, x, z, s = 1, wallCol = '#e8d5b7', roofCol = '#a8442f') {
  const g = new THREE.Group();
  const w = 2.6 * s, h = 2.0 * s, d = 2.4 * s;
  g.add(box(w, h, d, paintedMaterial('plaster', wallCol, { seed: Math.floor(rng() * 99) }), 0, h / 2, 0));
  const roof = cone(w * 0.86, 1.3 * s, 4, paintedMaterial('stone', roofCol, { seed: 11 }), 0, h + 0.62 * s, 0);
  roof.rotation.y = Math.PI / 4;
  g.add(roof);
  // door
  g.add(box(0.5 * s, 0.9 * s, 0.1, paintedMaterial('wood', '#6b4326', { seed: 9 }), 0, 0.45 * s, d / 2 + 0.04));
  // window
  g.add(box(0.45 * s, 0.45 * s, 0.08, flatMaterial('#ffe9a8', { emissive: '#3a2d00' }), w / 4, h * 0.62, d / 2 + 0.04, false));
  g.position.set(x, 0, z);
  g.rotation.y = (rng() - 0.5) * 0.6;
  return g;
}

function lamp(x, z) {
  const g = new THREE.Group();
  g.add(cyl(0.06, 0.09, 2.6, 8, paintedMaterial('metal', '#3b3b46', { seed: 2 }), 0, 1.3, 0));
  const bulb = sphere(0.2, flatMaterial('#ffe9a8', { emissive: '#8a6f1a' }), 0, 2.7, 0, true);
  g.add(bulb);
  const light = new THREE.PointLight('#ffcf7a', 1.6, 9, 2);
  light.position.set(0, 2.6, 0);
  g.add(light);
  g.position.set(x, 0, z);
  return g;
}

function cloud(rng, x, y, z, s = 1) {
  const g = new THREE.Group();
  const mat = paintedMaterial('sky', '#ffffff', { seed: 21 });
  const n = 4 + Math.floor(rng() * 3);
  for (let i = 0; i < n; i++) {
    g.add(sphere((0.7 + rng() * 0.5) * s, mat, (i - n / 2) * 0.7 * s, rng() * 0.4 * s, rng() * 0.4 * s, true));
  }
  g.position.set(x, y, z);
  return g;
}

/* ------------------------------------------------------------------ *
 * Environments. Each builder returns { group, updaters[] }.
 * Everything is primitives + procedural painted textures, so the same
 * environment always looks the same for a given variant seed.
 * ------------------------------------------------------------------ */
const ENV_BUILDERS = {
  town(rng) {
    const g = new THREE.Group();
    const upd = [];
    g.add(ground('grass', '#7fae5e', 90));
    // road
    const road = plane(6, 90, paintedMaterial('stone', '#5a5a63', { repeat: 10, seed: 4 }), { x: 0, y: 0.02, z: 0, rx: -Math.PI / 2 });
    g.add(road);
    for (let i = -4; i <= 4; i++) {
      g.add(plane(0.18, 1.4, flatMaterial('#f0e9c9'), { x: 0, y: 0.04, z: i * 8, rx: -Math.PI / 2 }));
    }
    const palette = ['#e8d5b7', '#dcc0a0', '#f0ddc2', '#cdb79c'];
    const roofs = ['#a8442f', '#8a3b52', '#4a5f8a', '#7a4a2a'];
    for (let i = 0; i < 8; i++) {
      const side = i % 2 === 0 ? -1 : 1;
      const x = side * (5.2 + rng() * 1.6);
      const z = -18 + i * 5 + rng() * 2;
      g.add(house(rng, x, z, 0.9 + rng() * 0.5, palette[i % palette.length], roofs[i % roofs.length]));
    }
    for (let i = 0; i < 10; i++) {
      g.add(tree(rng, (rng() - 0.5) * 40, -30 + rng() * 60, 0.8 + rng() * 0.7));
    }
    g.add(lamp(-3.6, -6));
    g.add(lamp(3.6, 6));
    for (let i = 0; i < 4; i++) {
      const c = cloud(rng, (rng() - 0.5) * 50, 9 + rng() * 5, -30 - rng() * 20, 1.4);
      g.add(c);
      upd.push((t) => { c.position.x += Math.sin(t * 0.15 + i) * 0.004; });
    }
    return { group: g, updaters: upd };
  },

  bridge(rng) {
    const g = new THREE.Group();
    const upd = [];
    const waterMat = paintedMaterial('water', '#3f7fa8', { repeat: 8, seed: 6 });
    const water = plane(120, 120, waterMat, { x: 0, y: -0.4, z: 0, rx: -Math.PI / 2 });
    g.add(water);
    const deckMat = paintedMaterial('stone', '#a89a86', { repeat: 4, seed: 8 });
    g.add(box(26, 0.5, 4.2, deckMat, 0, 1.2, 0));
    // arches
    for (const x of [-6, 0, 6]) {
      const arch = new THREE.Mesh(new THREE.TorusGeometry(2.2, 0.32, 10, 24, Math.PI), deckMat);
      arch.position.set(x, 1.2, 0);
      arch.rotation.z = Math.PI;
      g.add(arch);
      if (R.inkOutlines) addInk(arch, ink);
    }
    // railings
    for (const z of [-2.1, 2.1]) {
      g.add(box(26, 0.12, 0.12, paintedMaterial('wood', '#7a5230', { seed: 3 }), 0, 2.2, z));
      for (let x = -12; x <= 12; x += 2) {
        g.add(box(0.12, 1.0, 0.12, paintedMaterial('wood', '#7a5230', { seed: 3 }), x, 1.7, z));
      }
    }
    for (const x of [-11, 11]) {
      const tower = cyl(0.9, 1.1, 4.2, 12, paintedMaterial('stone', '#b7a794', { seed: 12 }), x, 2.1, 0);
      g.add(tower);
      g.add(cone(1.3, 1.6, 12, paintedMaterial('stone', '#8a3b52', { seed: 13 }), x, 5.0, 0));
    }
    upd.push((t) => { waterMat.map.offset.x = t * 0.02; waterMat.map.offset.y = Math.sin(t * 0.4) * 0.02; });
    return { group: g, updaters: upd };
  },

  ship(rng) {
    const g = new THREE.Group();
    const upd = [];
    const waterMat = paintedMaterial('water', '#2f6f96', { repeat: 10, seed: 14 });
    const water = plane(140, 140, waterMat, { x: 0, y: -0.5, z: 0, rx: -Math.PI / 2 });
    g.add(water);
    const ship = new THREE.Group();
    const hullMat = paintedMaterial('wood', '#7a4a2a', { repeat: 2, seed: 15 });
    const hull = box(7, 1.6, 3, hullMat, 0, 0.6, 0);
    ship.add(hull);
    const bow = new THREE.Mesh(new THREE.ConeGeometry(1.5, 2.6, 4), hullMat);
    bow.rotation.x = -Math.PI / 2;
    bow.rotation.y = Math.PI / 4;
    bow.position.set(0, 0.6, -2.6);
    ship.add(bow);
    ship.add(cyl(0.14, 0.18, 7, 8, paintedMaterial('wood', '#5a3a22', { seed: 16 }), 0, 4.2, 0));
    const sailMat = paintedMaterial('fabric', '#f2ead8', { seed: 17 });
    for (const [z, w, h, y] of [[-1.1, 3.6, 3.4, 4.2], [0.6, 4.4, 4.2, 4.6]]) {
      const sail = plane(w, h, sailMat, { x: 0, y, z });
      sail.rotation.y = Math.PI / 2;
      sail.material.side = THREE.DoubleSide;
      ship.add(sail);
    }
    const flag = plane(1.2, 0.7, paintedMaterial('fabric', '#c0392b', { seed: 18 }), { x: 0, y: 7.6, z: 0.4 });
    flag.material.side = THREE.DoubleSide;
    ship.add(flag);
    g.add(ship);
    for (let i = 0; i < 5; i++) g.add(cloud(rng, (rng() - 0.5) * 60, 10 + rng() * 6, -40 - rng() * 25, 1.6));
    upd.push((t) => {
      ship.position.y = Math.sin(t * 0.9) * 0.18;
      ship.rotation.z = Math.sin(t * 0.7) * 0.045;
      ship.rotation.x = Math.sin(t * 0.55 + 1) * 0.02;
      flag.rotation.z = Math.sin(t * 4) * 0.16;
      waterMat.map.offset.y = t * 0.03;
    });
    return { group: g, updaters: upd };
  },

  castle(rng) {
    const g = new THREE.Group();
    const upd = [];
    g.add(ground('grass', '#6f9c56', 100));
    const stoneMat = paintedMaterial('stone', '#b9b0a4', { repeat: 2, seed: 19 });
    const wall = 1.8;
    // curtain walls
    for (const [x, z, w, d] of [[0, -9, 26, 1.6], [0, 9, 26, 1.6], [-12, 0, 1.6, 18], [12, 0, 1.6, 18]]) {
      g.add(box(w, 4.2, d, stoneMat, x, 2.1, z));
      // crenellations
      const count = Math.floor(Math.max(w, d) / 2);
      for (let i = 0; i < count; i++) {
        const t2 = (i - (count - 1) / 2) * 2;
        if (w > d) g.add(box(1, 0.9, 1.8, stoneMat, x + t2, 4.6, z));
        else g.add(box(1.8, 0.9, 1, stoneMat, x, 4.6, z + t2));
      }
    }
    // towers
    for (const [x, z] of [[-13, -10], [13, -10], [-13, 10], [13, 10]]) {
      g.add(cyl(1.5, 1.7, 7, 14, stoneMat, x, 3.5, z));
      const roof = cone(2.0, 2.6, 14, paintedMaterial('stone', '#8a3b52', { seed: 20 }), x, 8.2, z);
      g.add(roof);
      const flag = plane(1.1, 0.6, paintedMaterial('fabric', '#f2c14b', { seed: 22 }), { x: x + 0.6, y: 9.9, z });
      flag.material.side = THREE.DoubleSide;
      g.add(flag);
      upd.push((t) => { flag.rotation.y = Math.sin(t * 2.2 + x) * 0.4; });
    }
    // keep
    g.add(box(7, 8, 7, stoneMat, 0, 4, 0));
    g.add(cone(5.2, 3.4, 4, paintedMaterial('stone', '#7a4a5f', { seed: 23 }), 0, 9.7, 0));
    g.add(box(2.4, 3.2, 0.3, paintedMaterial('wood', '#5a3a22', { seed: 24 }), 0, 1.6, 9.2));
    for (let i = 0; i < 12; i++) g.add(tree(rng, (rng() - 0.5) * 60, -22 + rng() * 44, 0.9 + rng() * 0.6));
    return { group: g, updaters: upd };
  },

  desert(rng) {
    const g = new THREE.Group();
    const upd = [];
    g.add(ground('sand', '#e0b878', 120));
    for (let i = 0; i < 9; i++) {
      const d = sphere(3 + rng() * 6, paintedMaterial('sand', '#d8ad6c', { seed: i }), (rng() - 0.5) * 70, -1.2, -15 - rng() * 40, false);
      d.scale.y = 0.35;
      g.add(d);
    }
    for (let i = 0; i < 5; i++) {
      const x = (rng() - 0.5) * 30;
      const z = -6 - rng() * 20;
      const cactus = new THREE.Group();
      cactus.add(cyl(0.28, 0.34, 2.4, 10, paintedMaterial('grass', '#4f8f4a', { seed: 31 }), 0, 1.2, 0));
      cactus.add(cyl(0.18, 0.2, 1.1, 8, paintedMaterial('grass', '#4f8f4a', { seed: 32 }), 0.55, 1.7, 0));
      cactus.add(cyl(0.16, 0.18, 0.9, 8, paintedMaterial('grass', '#4f8f4a', { seed: 33 }), -0.45, 2.0, 0));
      cactus.position.set(x, 0, z);
      g.add(cactus);
    }
    for (let i = 0; i < 8; i++) {
      const r = 0.4 + rng() * 1.1;
      g.add(sphere(r, paintedMaterial('stone', '#b09070', { seed: i + 40 }), (rng() - 0.5) * 40, r * 0.5, -4 - rng() * 30, true));
    }
    const sun = sphere(3.4, flatMaterial('#ffe08a', { emissive: '#ffb84a' }), 6, 12, -40, false);
    g.add(sun);
    for (let i = 0; i < 4; i++) g.add(cloud(rng, (rng() - 0.5) * 50, 8 + rng() * 4, -35 - rng() * 20, 1.2));
    upd.push((t) => { sun.position.y = 12 + Math.sin(t * 0.2) * 0.2; });
    return { group: g, updaters: upd };
  },

  lab(rng) {
    const g = new THREE.Group();
    const upd = [];
    g.add(plane(30, 30, paintedMaterial('metal', '#6f7785', { repeat: 6, seed: 44 }), { x: 0, y: 0, z: 0, rx: -Math.PI / 2 }));
    g.add(box(24, 8, 0.4, paintedMaterial('plaster', '#9aa4b2', { seed: 45 }), 0, 4, -8));
    for (const x of [-11, 11]) g.add(box(0.4, 8, 16, paintedMaterial('plaster', '#8b95a3', { seed: 46 }), x, 4, 0));
    // benches
    for (const z of [-3.5, 2.5]) {
      g.add(box(12, 0.25, 1.6, paintedMaterial('metal', '#c8ced8', { seed: 47 }), 0, 1.1, z));
      for (const x of [-5, 5]) g.add(box(0.3, 1.1, 1.4, paintedMaterial('metal', '#8b95a3', { seed: 48 }), x, 0.55, z));
    }
    // beakers + flasks
    const glass = new THREE.MeshToonMaterial({ color: '#a8e0ff', transparent: true, opacity: 0.55 });
    for (let i = 0; i < 7; i++) {
      const x = -5 + i * 1.7;
      const h = 0.5 + rng() * 0.6;
      g.add(cyl(0.22, 0.26, h, 10, glass, x, 1.25 + h / 2, -3.5, true));
      const liq = flatMaterial(['#4fe0a0', '#ff9f43', '#ff5f6d', '#5f8bff'][i % 4], { emissive: '#123' });
      g.add(cyl(0.2, 0.23, h * 0.5, 10, liq, x, 1.3 + h * 0.25, -3.5, false));
    }
    // monitors
    for (const [x, y] of [[-3.5, 2.2], [0, 2.6], [3.5, 2.2]]) {
      const scr = box(2.4, 1.4, 0.12, flatMaterial('#2fd0e0', { emissive: '#0b4a55' }), x, y, -7.6);
      g.add(scr);
      upd.push((t) => { scr.material.emissive.setHSL(0.52, 0.7, 0.18 + 0.06 * Math.sin(t * 3 + x)); });
    }
    // pipes on the ceiling
    for (let i = 0; i < 3; i++) {
      const p = cyl(0.16, 0.16, 22, 8, paintedMaterial('metal', '#7f8794', { seed: 50 }), 0, 7.2, -6 + i * 3);
      p.rotation.z = Math.PI / 2;
      g.add(p);
    }
    return { group: g, updaters: upd };
  },

  palace(rng) {
    const g = new THREE.Group();
    const upd = [];
    g.add(ground('stone', '#e6dcc4', 90));
    const marble = paintedMaterial('plaster', '#f2ecdd', { repeat: 2, seed: 55 });
    const gold = paintedMaterial('metal', '#d8b25a', { repeat: 2, seed: 56 });
    // steps
    for (let i = 0; i < 4; i++) g.add(box(18 - i * 2, 0.4, 12 - i * 2, marble, 0, 0.2 + i * 0.4, 6));
    // columns
    for (const x of [-7, -4, 4, 7]) {
      g.add(cyl(0.42, 0.5, 7, 14, marble, x, 5.5, 0));
      g.add(box(1.3, 0.4, 1.3, gold, x, 9.2, 0));
      g.add(box(1.3, 0.4, 1.3, gold, x, 1.9, 0));
    }
    g.add(box(17, 0.8, 8, marble, 0, 9.8, 0));
    g.add(box(15, 3.6, 7, marble, 0, 12, -0.5));
    // domes
    const dome = new THREE.Mesh(new THREE.SphereGeometry(3.2, 24, 14, 0, Math.PI * 2, 0, Math.PI / 2), gold);
    dome.position.set(0, 13.8, -0.5);
    g.add(dome);
    if (R.inkOutlines) addInk(dome, ink);
    for (const x of [-6, 6]) {
      const d2 = new THREE.Mesh(new THREE.SphereGeometry(1.8, 18, 12, 0, Math.PI * 2, 0, Math.PI / 2), gold);
      d2.position.set(x, 13.9, -0.5);
      g.add(d2);
      if (R.inkOutlines) addInk(d2, ink);
    }
    // arches
    for (const x of [-2, 2]) {
      const a = new THREE.Mesh(new THREE.TorusGeometry(1.5, 0.22, 8, 20, Math.PI), marble);
      a.position.set(x, 11.4, 3.1);
      g.add(a);
      if (R.inkOutlines) addInk(a, ink);
    }
    for (let i = 0; i < 8; i++) g.add(tree(rng, (rng() - 0.5) * 50, -14 - rng() * 20, 0.8 + rng() * 0.5));
    return { group: g, updaters: upd };
  },

  mountains(rng) {
    const g = new THREE.Group();
    const upd = [];
    g.add(ground('grass', '#7aa05a', 140));
    for (let i = 0; i < 10; i++) {
      const h = 12 + rng() * 22;
      const r = h * (0.55 + rng() * 0.2);
      const x = (rng() - 0.5) * 90;
      const z = -30 - rng() * 50;
      const m = cone(r, h, 7, paintedMaterial('stone', ['#8b8f9c', '#7a7f8c', '#9aa0ab'][i % 3], { seed: i }), x, h / 2, z);
      m.rotation.y = rng() * Math.PI;
      g.add(m);
      const cap = cone(r * 0.42, h * 0.28, 7, paintedMaterial('plaster', '#f4f7fb', { seed: i + 3 }), x, h - h * 0.14, z);
      cap.rotation.y = m.rotation.y;
      g.add(cap);
    }
    for (let i = 0; i < 14; i++) g.add(tree(rng, (rng() - 0.5) * 70, -8 - rng() * 40, 0.7 + rng() * 0.8));
    for (let i = 0; i < 5; i++) g.add(cloud(rng, (rng() - 0.5) * 60, 14 + rng() * 6, -50 - rng() * 30, 1.8));
    return { group: g, updaters: upd };
  },

  house(rng) {
    const g = new THREE.Group();
    const upd = [];
    g.add(ground('grass', '#7fae5e', 60));
    // pushed back and smaller so a character standing at the origin is never
    // buried in a wall, and a wide shot still reads as a house
    g.add(house(rng, 0, -8, 1.45, '#f0ddc2', '#a8442f'));
    g.add(plane(3.0, 10, paintedMaterial('stone', '#c2b49a', { repeat: 3, seed: 61 }), { x: 0, y: 0.03, z: 1.5, rx: -Math.PI / 2 }));
    // fence, well behind the character
    for (let x = -6; x <= 6; x += 1.2) {
      g.add(box(0.14, 1.1, 0.14, paintedMaterial('wood', '#8a6a42', { seed: 62 }), x, 0.55, 6.5));
    }
    g.add(box(13, 0.12, 0.12, paintedMaterial('wood', '#8a6a42', { seed: 62 }), 0, 0.85, 6.5));
    g.add(tree(rng, -5.5, -1.0, 1.3));
    g.add(tree(rng, 5.8, -2.0, 1.0));
    for (let i = 0; i < 3; i++) g.add(cloud(rng, (rng() - 0.5) * 30, 10 + rng() * 3, -20 - rng() * 15, 1.3));
    return { group: g, updaters: upd };
  },

  road(rng) {
    const g = new THREE.Group();
    const upd = [];
    g.add(ground('grass', '#6f9c56', 80));
    g.add(plane(10, 100, paintedMaterial('stone', '#4e4e57', { repeat: 12, seed: 63 }), { x: 0, y: 0.02, z: 0, rx: -Math.PI / 2 }));
    for (let i = -6; i <= 6; i++) {
      g.add(plane(0.3, 3, flatMaterial('#f2ead8'), { x: 0, y: 0.04, z: i * 8, rx: -Math.PI / 2 }));
    }
    const carCols = ['#c0392b', '#2f6fb5', '#f2c14b'];
    for (let i = 0; i < 3; i++) {
      const car = new THREE.Group();
      car.add(box(2.0, 0.8, 3.6, paintedMaterial('metal', carCols[i], { seed: 64 }), 0, 0.7, 0));
      car.add(box(1.7, 0.6, 1.8, flatMaterial('#8fd3ef'), 0, 1.35, -0.2));
      for (const [x, z] of [[-0.95, 1.2], [0.95, 1.2], [-0.95, -1.2], [0.95, -1.2]]) {
        const wheel = cyl(0.35, 0.35, 0.24, 12, flatMaterial('#22222a'), x, 0.36, z);
        wheel.rotation.z = Math.PI / 2;
        car.add(wheel);
      }
      car.position.set(i % 2 === 0 ? 2.5 : -2.5, 0, -6 - i * 10);
      car.rotation.y = i % 2 === 0 ? 0 : Math.PI;
      g.add(car);
      upd.push((t) => { car.position.z += Math.sin(t * 0.6 + i) * 0.004; });
    }
    for (let i = 0; i < 10; i++) g.add(tree(rng, (rng() - 0.5) * 40, -20 + rng() * 40, 0.7 + rng() * 0.6));
    return { group: g, updaters: upd };
  },

  trees(rng) {
    const g = new THREE.Group();
    const upd = [];
    g.add(ground('grass', '#5f8f4c', 100));
    for (let i = 0; i < 40; i++) g.add(tree(rng, (rng() - 0.5) * 70, -40 + rng() * 60, 0.8 + rng() * 1.2));
    for (let i = 0; i < 5; i++) g.add(cloud(rng, (rng() - 0.5) * 50, 11 + rng() * 4, -40 - rng() * 20, 1.5));
    return { group: g, updaters: upd };
  },

  water(rng) {
    const g = new THREE.Group();
    const upd = [];
    const waterMat = paintedMaterial('water', '#3f8fb8', { repeat: 10, seed: 66 });
    const water = plane(140, 140, waterMat, { x: 0, y: -0.1, z: 0, rx: -Math.PI / 2 });
    g.add(water);
    for (let i = 0; i < 8; i++) {
      const r = 1 + rng() * 2.4;
      g.add(sphere(r, paintedMaterial('stone', '#9a9086', { seed: i }), (rng() - 0.5) * 30, -0.2, -4 - rng() * 18, true));
    }
    for (let i = 0; i < 6; i++) g.add(tree(rng, (rng() - 0.5) * 50, -26 - rng() * 18, 1.1));
    upd.push((t) => { waterMat.map.offset.x = t * 0.015; });
    return { group: g, updaters: upd };
  },

  clouds(rng) {
    const g = new THREE.Group();
    const upd = [];
    const floor = plane(60, 60, paintedMaterial('sky', '#cfe8ff', { repeat: 6, seed: 67 }), { x: 0, y: -0.2, z: 0, rx: -Math.PI / 2 });
    g.add(floor);
    for (let i = 0; i < 14; i++) {
      const c = cloud(rng, (rng() - 0.5) * 44, rng() * 8, -30 + rng() * 40, 1 + rng() * 1.2);
      g.add(c);
      upd.push((t) => { c.position.x += Math.sin(t * 0.2 + i) * 0.01; });
    }
    return { group: g, updaters: upd };
  },

  plain(rng) {
    const g = new THREE.Group();
    const upd = [];
    g.add(ground('grass', '#79a75c', 140));
    for (let i = 0; i < 18; i++) {
      const r = 0.3 + rng() * 0.8;
      g.add(sphere(r, paintedMaterial('stone', '#a89a86', { seed: i }), (rng() - 0.5) * 60, r * 0.4, -8 - rng() * 50, true));
    }
    for (let i = 0; i < 8; i++) g.add(tree(rng, (rng() - 0.5) * 70, -20 - rng() * 40, 0.9 + rng() * 0.8));
    for (let i = 0; i < 5; i++) g.add(cloud(rng, (rng() - 0.5) * 60, 12 + rng() * 5, -45 - rng() * 25, 1.6));
    return { group: g, updaters: upd };
  },
};

/* ------------------------------------------------------------------ *
 * Lighting presets
 * ------------------------------------------------------------------ */
const LIGHT_PRESETS = {
  dawn: { bg: ['#9ec6e8', '#ffd9a8'], hemi: ['#cfe6ff', '#e8c8a0', 0.85], sun: ['#ffd9a0', 2.2, [6, 7, 6]], fog: ['#e8d8c0', 30, 120], ambient: 0.25, tint: '#fff0dd' },
  day: { bg: ['#7ec8f0', '#dff1ff'], hemi: ['#dff3ff', '#cfe0b0', 1.0], sun: ['#fff6e0', 2.6, [7, 12, 6]], fog: ['#cfe8f5', 45, 160], ambient: 0.3, tint: '#ffffff' },
  dusk: { bg: ['#4a5a8a', '#ff9e6b'], hemi: ['#8a96d8', '#d89a7a', 1.0], sun: ['#ffb27a', 2.2, [-7, 5, 5]], fog: ['#9a7a90', 30, 130], ambient: 0.45, tint: '#ffe4d0' },
  night: { bg: ['#1a2648', '#3a4a7a'], hemi: ['#8a9ada', '#3a4060', 1.15], sun: ['#c8dcff', 1.8, [-5, 9, 4]], fog: ['#202a4a', 30, 130], ambient: 0.55, tint: '#d8e4ff' },
  storm: { bg: ['#4a5568', '#8a94a8'], hemi: ['#9aa8bd', '#4a5262', 0.7], sun: ['#c8d4e8', 1.2, [-4, 10, 3]], fog: ['#6a7488', 15, 80], ambient: 0.3, tint: '#cfd8e8' },
  interior: { bg: ['#4a5064', '#8a90a8'], hemi: ['#e8ecf8', '#5a6074', 1.15], sun: ['#ffffff', 2.0, [4, 9, 6]], fog: ['#6a7088', 24, 90], ambient: 0.62, tint: '#f4f8ff' },
  magic: { bg: ['#3a2a5a', '#8a5ac8'], hemi: ['#d8b8ff', '#5a3a8a', 1.05], sun: ['#e8c8ff', 2.0, [5, 8, 5]], fog: ['#4a3a6a', 26, 110], ambient: 0.5, tint: '#f0e4ff' },
};

function applyLighting(scene, name) {
  const p = LIGHT_PRESETS[name] || LIGHT_PRESETS.day;
  scene.background = skyTexture(p.bg[0], p.bg[1]);
  scene.fog = new THREE.Fog(new THREE.Color(p.fog[0]), p.fog[1], p.fog[2]);

  if (R.lighting) {
    R.lighting.traverse((o) => {
      if (o.isLight) o.dispose && o.dispose();
    });
    scene.remove(R.lighting);
  }
  const group = new THREE.Group();
  const hemi = new THREE.HemisphereLight(p.hemi[0], p.hemi[1], p.hemi[2]);
  group.add(hemi);
  const amb = new THREE.AmbientLight('#ffffff', p.ambient);
  group.add(amb);
  const sun = new THREE.DirectionalLight(p.sun[0], p.sun[1]);
  sun.position.set(...p.sun[2]);
  sun.castShadow = true;
  sun.shadow.mapSize.set(R.shadowQuality, R.shadowQuality);
  const d = 22;
  sun.shadow.camera.left = -d;
  sun.shadow.camera.right = d;
  sun.shadow.camera.top = d;
  sun.shadow.camera.bottom = -d;
  sun.shadow.camera.far = 80;
  sun.shadow.bias = -0.0015;
  group.add(sun);
  group.add(sun.target);
  scene.add(group);
  R.lighting = group;
  R.lightTint = p.tint;
  return p;
}

/* ------------------------------------------------------------------ *
 * Characters as 2D cards inside the 3D world
 * ------------------------------------------------------------------ */
function makeCharacterCard(spec) {
  const canvas = document.createElement('canvas');
  canvas.width = CARD_W;
  canvas.height = CARD_H;
  const ctx = canvas.getContext('2d');
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.minFilter = THREE.LinearFilter;
  const mat = new THREE.MeshBasicMaterial({ map: texture, transparent: true, depthWrite: false, side: THREE.DoubleSide });
  const height = 2.3;
  const width = height * (CARD_W / CARD_H);
  const mesh = new THREE.Mesh(new THREE.PlaneGeometry(width, height), mat);
  mesh.position.y = height / 2;
  mesh.castShadow = false;
  mesh.renderOrder = 3;
  const group = new THREE.Group();
  group.add(mesh);
  return { canvas, ctx, texture, mat, mesh, group, height };
}

function ensureCharacters(plan) {
  for (const spec of plan.characters) {
    if (!R.charCards.has(spec.id)) {
      const card = makeCharacterCard(spec);
      card.group.visible = false;
      R.scene.add(card.group); // the card must be in the scene graph to render
      R.charCards.set(spec.id, card);
    }
  }
}

/* ------------------------------------------------------------------ *
 * Particles / VFX
 * ------------------------------------------------------------------ */
const VFX_DEFS = {
  dust: { count: 90, color: '#e8d8c0', size: 0.10, spread: 12, speed: 0.25, y: 0.4 },
  impact: { count: 60, color: '#ffe0a0', size: 0.14, spread: 4, speed: 2.4, y: 1.0 },
  sparkle: { count: 70, color: '#fff2b0', size: 0.09, spread: 8, speed: 0.6, y: 1.6 },
  rain: { count: 400, color: '#cfe8ff', size: 0.06, spread: 20, speed: 12, y: 8 },
  smoke: { count: 70, color: '#d0d0d8', size: 0.28, spread: 5, speed: 0.7, y: 1.2 },
  confetti: { count: 120, color: '#ffd0e0', size: 0.1, spread: 8, speed: 1.4, y: 3 },
};

function buildParticles(names) {
  const group = new THREE.Group();
  const updaters = [];
  for (const name of names) {
    const def = VFX_DEFS[name];
    if (!def) continue;
    const rnd = mulberry32(1234 + name.length * 97);
    const positions = new Float32Array(def.count * 3);
    const phases = new Float32Array(def.count);
    for (let i = 0; i < def.count; i++) {
      positions[i * 3] = (rnd() - 0.5) * def.spread;
      positions[i * 3 + 1] = def.y + rnd() * def.spread * 0.4;
      positions[i * 3 + 2] = (rnd() - 0.5) * def.spread * 0.7;
      phases[i] = rnd();
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    const mat = new THREE.PointsMaterial({
      map: softDiscTexture(def.color, 0.4),
      size: def.size,
      transparent: true,
      opacity: name === 'rain' ? 0.65 : 0.8,
      depthWrite: false,
      sizeAttenuation: true,
      blending: THREE.AdditiveBlending,
    });
    const points = new THREE.Points(geo, mat);
    points.name = `vfx-${name}`;
    group.add(points);
    updaters.push((t) => {
      const arr = geo.attributes.position.array;
      for (let i = 0; i < def.count; i++) {
        const ph = phases[i];
        if (name === 'rain') {
          arr[i * 3 + 1] = def.y - ((t * def.speed + ph * def.y) % def.y);
        } else {
          arr[i * 3 + 1] += def.speed * 0.012 * (0.5 + ph);
          arr[i * 3] += Math.sin(t * 0.8 + ph * 6.28) * 0.004;
          if (arr[i * 3 + 1] > def.y + def.spread * 0.5) arr[i * 3 + 1] = def.y;
        }
      }
      geo.attributes.position.needsUpdate = true;
      mat.opacity = (name === 'impact' ? 0.9 : 0.8) * (0.7 + 0.3 * Math.sin(t * 2));
    });
  }
  return { group, updaters };
}

/* ------------------------------------------------------------------ *
 * Scene assembly for a shot
 * ------------------------------------------------------------------ */
function envKey(shot) {
  return `${shot.environment}|${shot.environmentVariant || 1}`;
}

function getEnvironment(shot) {
  const key = envKey(shot);
  if (!R.envs.has(key)) {
    const builder = ENV_BUILDERS[shot.environment] || ENV_BUILDERS.plain;
    const rnd = mulberry32(hashKey(key));
    const built = builder(rnd);
    built.group.visible = false;
    R.scene.add(built.group);
    R.envs.set(key, built);
  }
  return R.envs.get(key);
}

function hashKey(s) {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

function placeCharacters(shot) {
  // hide everyone first
  for (const card of R.charCards.values()) card.group.visible = false;

  const list = shot.characters && shot.characters.length ? shot.characters : [];
  const placed = [];
  list.forEach((sc, i) => {
    let spec = R.plan.characters.find((c) => c.id === sc.id);
    if (!spec) {
      // planner invented a character that was never declared: build it stably
      spec = { ...characterFromSeed(sc.id, sc.id), id: sc.id };
      R.plan.characters.push(spec);
      ensureCharacters(R.plan);
    }
    const card = R.charCards.get(spec.id);
    if (!card) return;
    card.group.visible = true;
    card.spec = spec;
    card.shotChar = sc;
    card.scaleFactor = sc.scale || 1;
    placed.push(card);
  });
  return placed;
}

function setupShot(shot, frame) {
  // environment
  const env = getEnvironment(shot);
  for (const [key, e] of R.envs) e.group.visible = key === envKey(shot);

  // lighting
  const lightPreset = applyLighting(R.scene, shot.lighting);
  const tint = new THREE.Color(lightPreset.tint);

  // characters
  const cards = placeCharacters(shot);
  for (const card of cards) card.mat.color.copy(tint);

  // vfx — R.vfxGroup is a plain {group, updaters} record, not an Object3D
  if (R.vfxGroup) {
    R.scene.remove(R.vfxGroup.group);
    R.vfxGroup.group.traverse((o) => {
      if (o.geometry) o.geometry.dispose();
      if (o.material) o.material.dispose();
    });
  }
  const vfxNames = shot.vfx && shot.vfx.length ? shot.vfx : ['dust'];
  R.vfxGroup = buildParticles(vfxNames);
  R.scene.add(R.vfxGroup.group);

  R.activeEnv = env;
  R.activeCards = cards;
  R.activeShot = shot;
  R.activeShotIndex = shot.index;
}

/* ------------------------------------------------------------------ *
 * Renderer + post-processing
 * ------------------------------------------------------------------ */
function initRenderer() {
  const canvas = document.getElementById('stage');
  canvas.width = R.width;
  canvas.height = R.height;
  const renderer = new THREE.WebGLRenderer({
    canvas,
    antialias: false, // CPU rendering: MSAA is too expensive; we render at 2x and downscale instead
    preserveDrawingBuffer: true,
    powerPreference: 'high-performance',
    alpha: false,
  });
  renderer.setPixelRatio(1);
  renderer.setSize(R.width, R.height, false);
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.15;
  R.renderer = renderer;

  const camera = new THREE.PerspectiveCamera(40, R.width / R.height, 0.1, 400);
  camera.position.set(0, 1.7, 7);
  R.camera = camera;

  const scene = new THREE.Scene();
  R.scene = scene;

  if (R.postprocessing) {
    try {
      const composer = new EffectComposer(renderer);
      composer.setPixelRatio(1);
      composer.setSize(R.width, R.height);
      composer.addPass(new RenderPass(scene, camera));
      const bokeh = new BokehPass(scene, camera, { focus: 6.0, aperture: 0.0016, maxblur: 0.006 });
      composer.addPass(bokeh);
      R.composer = composer;
      R.bokeh = bokeh;
    } catch (err) {
      console.warn('post-processing unavailable, falling back to direct render:', err && err.message);
      R.composer = null;
      R.bokeh = null;
    }
  }
}

/* ------------------------------------------------------------------ *
 * The frame function
 * ------------------------------------------------------------------ */
function renderFrame(frameIndex) {
  const plan = R.plan;
  const frame = Math.max(0, Math.min(plan.totalFrames - 1, frameIndex | 0));
  const shot = shotAt(plan, frame);
  if (!shot) throw new Error('no shot for frame ' + frame);
  const t = frame / plan.fps;
  const p = shotProgress(shot, frame);

  if (shot.index !== R.activeShotIndex) setupShot(shot, frame);

  // ---- camera ----
  const cam = shotCamera(shot, p, t);
  R.camera.position.set(cam.pos[0], cam.pos[1], cam.pos[2]);
  R.camera.fov = cam.fov;
  R.camera.updateProjectionMatrix();
  // keep the acting character near the centre of frame, even when the planner
  // places them off-axis
  const lead = R.activeCards && R.activeCards[0] ? R.activeCards[0].group.position : null;
  const lookX = lead ? cam.look[0] + (lead.x - cam.look[0]) * 0.55 : cam.look[0];
  R.camera.lookAt(lookX, cam.look[1], cam.look[2]);

  // ---- characters ----
  let focusDist = 6;
  for (const card of R.activeCards) {
    const st = characterSample(shot, card.shotChar, p, t, plan);
    card.ctx.clearRect(0, 0, CARD_W, CARD_H);
    // toon.js draws into its own ctx; we reuse the same drawing routine
    drawInto(card, st);
    card.texture.needsUpdate = true;

    const s = card.scaleFactor;
    card.group.position.set(st.x, st.y, st.z);
    card.group.scale.setScalar(s);
    // 2D card always faces the camera (billboard), with a slight lean
    card.group.lookAt(R.camera.position.x, card.group.position.y + 1.3 * s, R.camera.position.z);
    if (st.facing < 0) card.mesh.scale.x = -1;
    else card.mesh.scale.x = 1;

    focusDist = R.camera.position.distanceTo(card.group.position.clone().setY(1.4));
  }

  // ---- environment + vfx animation ----
  if (R.activeEnv) for (const fn of R.activeEnv.updaters) fn(t);
  if (R.vfxGroup) for (const fn of R.vfxGroup.updaters) fn(t);

  // ---- depth of field ----
  if (R.bokeh) {
    R.bokeh.uniforms.focus.value = focusDist;
    R.bokeh.uniforms.aperture.value = shot.shotSize === 'close-up' ? 0.0035 : 0.0014;
    R.bokeh.uniforms.maxblur.value = shot.shotSize === 'close-up' ? 0.012 : 0.006;
  }

  // ---- draw ----
  if (R.composer) R.composer.render();
  else R.renderer.render(R.scene, R.camera);

  return { frame, shot: shot.index, t };
}

/* Draw the character through toon.js into the card's own canvas. */
function drawInto(card, st) {
  drawCharacter(card.ctx, card.spec, st);
}

/* ------------------------------------------------------------------ *
 * Boot
 * ------------------------------------------------------------------ */
async function boot() {
  const params = new URLSearchParams(location.search);
  const planUrl = params.get('plan') || '/work/plan.json';
  const res = await fetch(planUrl, { cache: 'no-store' });
  if (!res.ok) throw new Error(`could not load plan: ${planUrl} (${res.status})`);
  const raw = await res.json();
  const plan = normalizePlan(raw);

  R.plan = plan;
  R.width = plan.width || 1080;
  R.height = plan.height || 1920;
  R.fps = plan.fps || 30;
  R.postprocessing = !(plan.render && plan.render.postprocessing === false);
  R.inkOutlines = !(plan.render && plan.render.inkOutlines === false);
  R.shadowQuality = (plan.render && plan.render.shadowQuality) || 1024;

  const problems = validatePlan(plan);
  if (problems.length) {
    // Non-fatal: report them, but still render whatever is valid.
    window.__planProblems = problems;
    console.warn('plan problems:', problems);
  }

  initRenderer();
  ensureCharacters(plan);

  window.renderFrame = (i) => renderFrame(i);
  window.__meta = {
    frames: plan.totalFrames,
    fps: plan.fps,
    width: R.width,
    height: R.height,
    shots: plan.shots.length,
    problems,
  };
  window.__ready = true;
}

boot().catch((err) => {
  window.__error = String((err && err.stack) || err);
  console.error('boot failed', err);
});
