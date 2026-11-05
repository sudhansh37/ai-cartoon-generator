// toon.js — the bean-head character drawing system.
//
// Every character is drawn procedurally onto a 512x1024 canvas and then used
// as a flat "2D card" inside the 3D world (see main.js). This keeps one single
// drawing architecture for every character, which is what makes identity stay
// consistent from shot to shot — the same spec always produces the same face,
// hair, outfit and body proportions.
//
// Draw order: shadow → legs → torso → arms → neck → head → hair → headgear
//             → face → accessories.

import { makeCanvas, mulberry32, shade } from './paint.js';

export const CARD_W = 512;
export const CARD_H = 1024;

const INK = '#2b1730';
const INK_W = 9;

export const POSES = ['stand', 'walk', 'run', 'sit', 'point', 'react', 'wave', 'carry'];
export const EXPRESSIONS = ['neutral', 'happy', 'sad', 'angry', 'surprised', 'scared', 'smug', 'determined'];

export const DEFAULT_CHARACTER = {
  id: 'hero',
  name: 'Hero',
  skin: '#f0c49a',
  hair: '#2a1a12',
  hairStyle: 'short', // short | long | bun | spiky | bald | curly
  outfit: '#c0392b',
  outfitStyle: 'tunic', // tunic | coat | dress | armor | apron | robe
  pants: '#2f3b52',
  shoe: '#3a2a1e',
  headgear: 'none', // none | cap | hat | crown | helmet | turban | hood
  accessory: 'none', // none | glasses | scarf | staff | bag
  height: 1.0,
  width: 1.0,
};

/* ----------------------------- primitives ----------------------------- */

function ellipse(ctx, x, y, rx, ry, rot = 0) {
  ctx.beginPath();
  ctx.ellipse(x, y, rx, ry, rot, 0, Math.PI * 2);
}

function inkShape(ctx, drawPath, fill, { lineWidth = INK_W, stroke = INK } = {}) {
  drawPath();
  ctx.fillStyle = fill;
  ctx.fill();
  if (lineWidth > 0) {
    ctx.lineWidth = lineWidth;
    ctx.strokeStyle = stroke;
    ctx.lineJoin = 'round';
    ctx.stroke();
  }
}

function capsule(ctx, x1, y1, x2, y2, r, fill) {
  ctx.beginPath();
  ctx.moveTo(x1, y1);
  ctx.lineTo(x2, y2);
  ctx.lineCap = 'round';
  ctx.lineWidth = r * 2;
  ctx.strokeStyle = fill;
  ctx.stroke();
  // ink outline: same capsule, slightly wider, drawn under
}

function capsuleInk(ctx, x1, y1, x2, y2, r, fill, lineWidth = INK_W) {
  ctx.beginPath();
  ctx.moveTo(x1, y1);
  ctx.lineTo(x2, y2);
  ctx.lineCap = 'round';
  ctx.lineWidth = r * 2 + lineWidth * 2;
  ctx.strokeStyle = INK;
  ctx.stroke();
  ctx.beginPath();
  ctx.moveTo(x1, y1);
  ctx.lineTo(x2, y2);
  ctx.lineWidth = r * 2;
  ctx.strokeStyle = fill;
  ctx.stroke();
}

function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

/* ----------------------------- pose model ----------------------------- *
 * All angles are in radians, measured from "straight down" for arms.
 * `phase` is the walk/run cycle phase in radians (0..2π).
 * -------------------------------------------------------------------- */
function poseRig(pose, phase, t) {
  const swing = Math.sin(phase);
  const rig = {
    bob: 0,
    lean: 0,
    armL: { angle: 0.12, bend: 0.15, ext: 1 },
    armR: { angle: -0.12, bend: 0.15, ext: 1 },
    legL: { angle: 0, bend: 0 },
    legR: { angle: 0, bend: 0 },
    sitOffset: 0,
    headTilt: 0,
  };

  switch (pose) {
    case 'walk':
      rig.armL.angle = 0.55 * swing;
      rig.armR.angle = -0.55 * swing;
      rig.legL.angle = 0.5 * swing;
      rig.legR.angle = -0.5 * swing;
      rig.bob = Math.abs(Math.cos(phase)) * 10;
      break;
    case 'run':
      rig.armL.angle = 0.95 * swing;
      rig.armR.angle = -0.95 * swing;
      rig.armL.bend = 1.1;
      rig.armR.bend = 1.1;
      rig.legL.angle = 0.85 * swing;
      rig.legR.angle = -0.85 * swing;
      rig.legL.bend = 0.5;
      rig.legR.bend = 0.5;
      rig.bob = Math.abs(Math.cos(phase)) * 20;
      rig.lean = 0.16;
      break;
    case 'sit':
      rig.legL = { angle: 1.35, bend: 1.2 };
      rig.legR = { angle: 1.35, bend: 1.2 };
      rig.sitOffset = 150;
      rig.armL.angle = 0.35;
      rig.armR.angle = -0.35;
      break;
    case 'point':
      rig.armR = { angle: -1.65, bend: 0.05, ext: 1.15 };
      rig.armL = { angle: 0.25, bend: 0.2 };
      rig.headTilt = -0.05;
      break;
    case 'react':
      rig.armL = { angle: -2.25 + 0.12 * Math.sin(t * 6), bend: 0.35 };
      rig.armR = { angle: 2.25 - 0.12 * Math.sin(t * 6), bend: 0.35 };
      rig.bob = 6 * Math.abs(Math.sin(t * 3));
      rig.headTilt = -0.08;
      break;
    case 'wave':
      rig.armR = { angle: 2.5 + 0.35 * Math.sin(t * 8), bend: 0.15, ext: 1.1 };
      rig.armL = { angle: 0.2, bend: 0.2 };
      break;
    case 'carry':
      rig.armL = { angle: -0.9, bend: 1.5 };
      rig.armR = { angle: 0.9, bend: 1.5 };
      break;
    case 'stand':
    default:
      // idle breathing — characters must never be completely frozen
      rig.bob = Math.sin(t * 2.0) * 3.5;
      rig.armL.angle = 0.12 + Math.sin(t * 2.0) * 0.03;
      rig.armR.angle = -0.12 - Math.sin(t * 2.0 + 0.7) * 0.03;
      break;
  }
  return rig;
}

function expressionFace(expr) {
  switch (expr) {
    case 'happy':
      return { brow: -0.18, browY: -6, eye: 1.05, mouth: 'smile', mouthW: 52, blush: 0.5 };
    case 'sad':
      return { brow: 0.32, browY: 4, eye: 0.85, mouth: 'frown', mouthW: 40, blush: 0.1 };
    case 'angry':
      return { brow: -0.45, browY: 8, eye: 0.8, mouth: 'grit', mouthW: 46, blush: 0.25 };
    case 'surprised':
      return { brow: -0.35, browY: -12, eye: 1.28, mouth: 'o', mouthW: 30, blush: 0.2 };
    case 'scared':
      return { brow: 0.4, browY: -8, eye: 1.3, mouth: 'gasp', mouthW: 34, blush: 0.05 };
    case 'smug':
      return { brow: -0.28, browY: -2, eye: 0.92, mouth: 'smirk', mouthW: 44, blush: 0.3 };
    case 'determined':
      return { brow: -0.35, browY: 3, eye: 0.95, mouth: 'line', mouthW: 42, blush: 0.15 };
    default:
      return { brow: 0, browY: 0, eye: 1.0, mouth: 'line', mouthW: 38, blush: 0.25 };
  }
}

/* ----------------------------- the drawing ----------------------------- */

export function drawCharacter(ctx, specIn, state = {}) {
  const spec = { ...DEFAULT_CHARACTER, ...specIn };
  const pose = POSES.includes(state.pose) ? state.pose : 'stand';
  const expr = EXPRESSIONS.includes(state.expression) ? state.expression : 'neutral';
  const t = state.t || 0;
  const phase = state.phase != null ? state.phase : t * 6.0;
  const blink = state.blink != null ? state.blink : 0; // 0 open → 1 closed
  const mouthOpen = state.mouthOpen != null ? state.mouthOpen : 0;

  const rig = poseRig(pose, phase, t);
  const face = expressionFace(expr);

  ctx.clearRect(0, 0, CARD_W, CARD_H);
  ctx.save();
  ctx.translate(CARD_W / 2, 0);
  ctx.scale(spec.width || 1, spec.height || 1);
  ctx.translate(-CARD_W / 2, 0);

  const cx = CARD_W / 2;
  const groundY = 986;
  const sit = rig.sitOffset;

  // ---- ground shadow ----
  const sg = ctx.createRadialGradient(cx, groundY + 6, 4, cx, groundY + 6, 130);
  sg.addColorStop(0, 'rgba(20,10,30,0.34)');
  sg.addColorStop(1, 'rgba(20,10,30,0)');
  ctx.fillStyle = sg;
  ellipse(ctx, cx, groundY + 6, 130, 26);
  ctx.fill();

  ctx.save();
  // forward lean for running
  if (rig.lean) {
    ctx.translate(cx, groundY);
    ctx.rotate(rig.lean);
    ctx.translate(-cx, -groundY);
  }
  ctx.translate(0, -rig.bob + sit);

  // ---- geometry anchors ----
  const hipY = 780;
  const shoulderY = 545;
  const headCY = 315;
  const headRX = 122;
  const headRY = 142;
  const torsoCY = 655;
  const torsoRX = 116;
  const torsoRY = 175;

  // ---- legs ----
  const legLen = 150;
  const drawLeg = (side, legRig) => {
    const hx = cx + side * 48;
    const ang = legRig.angle;
    const kneeX = hx + Math.sin(ang) * legLen * 0.55;
    const kneeY = hipY + Math.cos(ang) * legLen * 0.55;
    const footAng = ang - (legRig.bend || 0);
    const footX = kneeX + Math.sin(footAng) * legLen * 0.55;
    const footY = kneeY + Math.cos(footAng) * legLen * 0.55;
    capsuleInk(ctx, hx, hipY, kneeX, kneeY, 30, spec.pants);
    capsuleInk(ctx, kneeX, kneeY, footX, footY, 26, spec.pants);
    // shoe
    ctx.save();
    ctx.translate(footX, footY + 8);
    ctx.rotate(footAng * 0.35);
    inkShape(ctx, () => roundRect(ctx, -44, -18, 88, 40, 16), spec.shoe, { lineWidth: 7 });
    ctx.restore();
  };

  drawLeg(-1, rig.legL);
  drawLeg(1, rig.legR);

  // ---- torso (bean body) ----
  inkShape(
    ctx,
    () => {
      ctx.beginPath();
      ctx.ellipse(cx, torsoCY, torsoRX, torsoRY, 0, 0, Math.PI * 2);
    },
    spec.outfit
  );

  // outfit detail by style
  ctx.save();
  ctx.lineWidth = 6;
  ctx.strokeStyle = shade(spec.outfit, -0.35);
  if (spec.outfitStyle === 'coat') {
    ctx.beginPath();
    ctx.moveTo(cx, torsoCY - torsoRY + 20);
    ctx.lineTo(cx, torsoCY + torsoRY - 20);
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(cx - torsoRX * 0.75, torsoCY - 40);
    ctx.lineTo(cx, torsoCY - 10);
    ctx.lineTo(cx + torsoRX * 0.75, torsoCY - 40);
    ctx.stroke();
  } else if (spec.outfitStyle === 'armor') {
    ctx.beginPath();
    ctx.ellipse(cx, torsoCY - 20, torsoRX * 0.82, torsoRY * 0.55, 0, 0, Math.PI * 2);
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(cx - torsoRX * 0.6, torsoCY + 40);
    ctx.lineTo(cx + torsoRX * 0.6, torsoCY + 40);
    ctx.stroke();
  } else if (spec.outfitStyle === 'dress') {
    ctx.beginPath();
    ctx.moveTo(cx - torsoRX * 0.9, torsoCY + 60);
    ctx.quadraticCurveTo(cx, torsoCY + 130, cx + torsoRX * 0.9, torsoCY + 60);
    ctx.stroke();
  } else if (spec.outfitStyle === 'apron') {
    inkShape(ctx, () => roundRect(ctx, cx - 62, torsoCY - 60, 124, 210, 18), shade(spec.outfit, 0.5), { lineWidth: 5 });
  } else if (spec.outfitStyle === 'robe') {
    ctx.beginPath();
    ctx.moveTo(cx - torsoRX * 0.5, torsoCY - 120);
    ctx.lineTo(cx + 10, torsoCY - 20);
    ctx.stroke();
  }
  // collar
  ctx.beginPath();
  ctx.moveTo(cx - 46, torsoCY - torsoRY + 12);
  ctx.quadraticCurveTo(cx, torsoCY - torsoRY + 46, cx + 46, torsoCY - torsoRY + 12);
  ctx.stroke();
  ctx.restore();

  // ---- arms ----
  const armLen = 165;
  const drawArm = (side, armRig) => {
    const sx = cx + side * (torsoRX - 12);
    const sy = shoulderY;
    const ang = side === -1 ? -armRig.angle : armRig.angle;
    const ex = sx + Math.sin(ang) * armLen * 0.62 * (armRig.ext || 1);
    const ey = sy + Math.cos(ang) * armLen * 0.62 * (armRig.ext || 1);
    const bend = armRig.bend || 0;
    const ang2 = ang - side * bend;
    const hx = ex + Math.sin(ang2) * armLen * 0.52;
    const hy = ey + Math.cos(ang2) * armLen * 0.52;
    capsuleInk(ctx, sx, sy, ex, ey, 25, spec.outfit);
    capsuleInk(ctx, ex, ey, hx, hy, 21, spec.outfit);
    // hand
    inkShape(ctx, () => { ctx.beginPath(); ctx.arc(hx, hy, 27, 0, Math.PI * 2); }, spec.skin, { lineWidth: 7 });
    // pointing index finger
    if (pose === 'point' && side === 1) {
      capsuleInk(ctx, hx, hy, hx + Math.sin(ang2) * 46, hy + Math.cos(ang2) * 46, 10, spec.skin, 6);
    }
    return { hx, hy };
  };

  drawArm(-1, rig.armL);
  drawArm(1, rig.armR);

  // ---- neck ----
  inkShape(ctx, () => roundRect(ctx, cx - 30, headCY + 90, 60, 70, 20), shade(spec.skin, -0.12), { lineWidth: 7 });

  // ---- head (bean) ----
  const tilt = (rig.headTilt || 0) + (state.headTurn || 0) * 0.12;
  ctx.save();
  ctx.translate(cx, headCY);
  ctx.rotate(tilt);
  ctx.translate(-cx, -headCY);

  inkShape(ctx, () => { ctx.beginPath(); ctx.ellipse(cx, headCY, headRX, headRY, 0, 0, Math.PI * 2); }, spec.skin);

  // ears
  inkShape(ctx, () => { ctx.beginPath(); ctx.arc(cx - headRX + 4, headCY + 12, 20, 0, Math.PI * 2); }, spec.skin, { lineWidth: 6 });
  inkShape(ctx, () => { ctx.beginPath(); ctx.arc(cx + headRX - 4, headCY + 12, 20, 0, Math.PI * 2); }, spec.skin, { lineWidth: 6 });

  // ---- hair ----
  const hairCol = spec.hair;
  ctx.save();
  switch (spec.hairStyle) {
    case 'long':
      inkShape(ctx, () => {
        ctx.beginPath();
        ctx.ellipse(cx, headCY - 20, headRX + 14, headRY + 6, 0, Math.PI, Math.PI * 2);
        ctx.lineTo(cx + headRX + 14, headCY + 210);
        ctx.quadraticCurveTo(cx, headCY + 150, cx - headRX - 14, headCY + 210);
        ctx.closePath();
      }, hairCol, { lineWidth: 8 });
      break;
    case 'bun':
      inkShape(ctx, () => { ctx.beginPath(); ctx.arc(cx, headCY - headRY - 6, 44, 0, Math.PI * 2); }, hairCol, { lineWidth: 8 });
      inkShape(ctx, () => {
        ctx.beginPath();
        ctx.ellipse(cx, headCY - 26, headRX + 4, headRY * 0.72, 0, Math.PI, Math.PI * 2);
        ctx.closePath();
      }, hairCol, { lineWidth: 8 });
      break;
    case 'spiky':
      inkShape(ctx, () => {
        ctx.beginPath();
        const n = 7;
        for (let i = 0; i <= n; i++) {
          const a = Math.PI + (i / n) * Math.PI;
          const r = headRX + 30 + (i % 2 ? 34 : 0);
          const x = cx + Math.cos(a) * r;
          const y = headCY + Math.sin(a) * r * 0.95;
          if (i === 0) ctx.moveTo(x, y);
          else ctx.lineTo(x, y);
        }
        ctx.closePath();
      }, hairCol, { lineWidth: 8 });
      break;
    case 'curly':
      for (let i = 0; i < 9; i++) {
        const a = Math.PI + (i / 8) * Math.PI;
        const x = cx + Math.cos(a) * (headRX - 6);
        const y = headCY + Math.sin(a) * (headRY - 4);
        inkShape(ctx, () => { ctx.beginPath(); ctx.arc(x, y, 34, 0, Math.PI * 2); }, hairCol, { lineWidth: 7 });
      }
      break;
    case 'bald':
      break;
    case 'short':
    default:
      inkShape(ctx, () => {
        ctx.beginPath();
        ctx.ellipse(cx, headCY - 30, headRX + 2, headRY * 0.76, 0, Math.PI, Math.PI * 2);
        ctx.closePath();
      }, hairCol, { lineWidth: 8 });
      break;
  }
  ctx.restore();

  // ---- headgear ----
  switch (spec.headgear) {
    case 'cap':
      inkShape(ctx, () => {
        ctx.beginPath();
        ctx.ellipse(cx, headCY - 52, headRX + 6, headRY * 0.66, 0, Math.PI, Math.PI * 2);
        ctx.closePath();
      }, '#2f6fb5', { lineWidth: 8 });
      inkShape(ctx, () => roundRect(ctx, cx - 4, headCY - 62, headRX + 52, 20, 9), '#24568c', { lineWidth: 7 });
      break;
    case 'hat':
      inkShape(ctx, () => { ctx.beginPath(); ctx.ellipse(cx, headCY - 62, headRX + 52, 22, 0, 0, Math.PI * 2); }, '#3b2b20', { lineWidth: 8 });
      inkShape(ctx, () => roundRect(ctx, cx - 78, headCY - 200, 156, 142, 18), '#4a3627', { lineWidth: 8 });
      break;
    case 'crown':
      inkShape(ctx, () => {
        ctx.beginPath();
        ctx.moveTo(cx - 74, headCY - 100);
        ctx.lineTo(cx - 74, headCY - 168);
        ctx.lineTo(cx - 30, headCY - 128);
        ctx.lineTo(cx, headCY - 182);
        ctx.lineTo(cx + 30, headCY - 128);
        ctx.lineTo(cx + 74, headCY - 168);
        ctx.lineTo(cx + 74, headCY - 100);
        ctx.closePath();
      }, '#f2c14b', { lineWidth: 7 });
      break;
    case 'helmet':
      inkShape(ctx, () => {
        ctx.beginPath();
        ctx.ellipse(cx, headCY - 18, headRX + 16, headRY * 0.9, 0, Math.PI, Math.PI * 2);
        ctx.closePath();
      }, '#8b98a8', { lineWidth: 8 });
      inkShape(ctx, () => roundRect(ctx, cx - 16, headCY - 40, 32, 120, 10), '#6f7b8a', { lineWidth: 6 });
      break;
    case 'turban':
      inkShape(ctx, () => {
        ctx.beginPath();
        ctx.ellipse(cx, headCY - 46, headRX + 18, headRY * 0.74, 0, Math.PI, Math.PI * 2);
        ctx.closePath();
      }, '#d9822b', { lineWidth: 8 });
      ctx.save();
      ctx.strokeStyle = '#a85f18';
      ctx.lineWidth = 6;
      ctx.beginPath();
      ctx.moveTo(cx - 90, headCY - 70);
      ctx.quadraticCurveTo(cx, headCY - 130, cx + 90, headCY - 60);
      ctx.stroke();
      ctx.restore();
      break;
    case 'hood':
      inkShape(ctx, () => {
        ctx.beginPath();
        ctx.ellipse(cx, headCY - 10, headRX + 22, headRY + 12, 0, Math.PI * 0.85, Math.PI * 2.15);
        ctx.closePath();
      }, shade(spec.outfit, -0.2), { lineWidth: 8 });
      break;
    default:
      break;
  }

  // ---- face ----
  const eyeY = headCY + 6;
  const eyeDX = 46;
  const eyeOpen = Math.max(0.06, (1 - blink) * face.eye);

  // eyes
  for (const side of [-1, 1]) {
    const ex = cx + side * eyeDX;
    inkShape(ctx, () => { ctx.beginPath(); ctx.ellipse(ex, eyeY, 24, 26 * eyeOpen, 0, 0, Math.PI * 2); }, '#ffffff', { lineWidth: 6 });
    if (eyeOpen > 0.25) {
      const look = (state.lookX || 0) * 6;
      ctx.fillStyle = INK;
      ctx.beginPath();
      ctx.ellipse(ex + look, eyeY + 2, 11, 13 * Math.min(1, eyeOpen), 0, 0, Math.PI * 2);
      ctx.fill();
      ctx.fillStyle = 'rgba(255,255,255,0.9)';
      ctx.beginPath();
      ctx.arc(ex + look - 4, eyeY - 5, 4, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  // brows
  ctx.save();
  ctx.strokeStyle = shade(spec.hair, -0.1);
  ctx.lineWidth = 9;
  ctx.lineCap = 'round';
  for (const side of [-1, 1]) {
    const bx = cx + side * eyeDX;
    const by = eyeY - 44 + face.browY;
    ctx.beginPath();
    ctx.moveTo(bx - 26, by + face.brow * 26 * side * -1);
    ctx.lineTo(bx + 26, by - face.brow * 26 * side * -1);
    ctx.stroke();
  }
  ctx.restore();

  // nose
  ctx.save();
  ctx.strokeStyle = shade(spec.skin, -0.35);
  ctx.lineWidth = 6;
  ctx.lineCap = 'round';
  ctx.beginPath();
  ctx.arc(cx, eyeY + 40, 12, 0.15 * Math.PI, 0.85 * Math.PI);
  ctx.stroke();
  ctx.restore();

  // mouth
  const my = eyeY + 84;
  const mw = face.mouthW;
  ctx.save();
  ctx.strokeStyle = INK;
  ctx.lineWidth = 8;
  ctx.lineCap = 'round';
  ctx.fillStyle = '#7a2b34';
  if (mouthOpen > 0.06) {
    // talking mouth
    const h = 10 + 34 * mouthOpen;
    inkShape(ctx, () => { ctx.beginPath(); ctx.ellipse(cx, my, mw * 0.7, h, 0, 0, Math.PI * 2); }, '#7a2b34', { lineWidth: 7 });
  } else {
    switch (face.mouth) {
      case 'smile':
        ctx.beginPath();
        ctx.arc(cx, my - 18, mw, 0.18 * Math.PI, 0.82 * Math.PI);
        ctx.stroke();
        break;
      case 'frown':
        ctx.beginPath();
        ctx.arc(cx, my + 26, mw, 1.18 * Math.PI, 1.82 * Math.PI);
        ctx.stroke();
        break;
      case 'grit':
        inkShape(ctx, () => roundRect(ctx, cx - mw, my - 12, mw * 2, 26, 8), '#f7f2ea', { lineWidth: 6 });
        ctx.beginPath();
        ctx.moveTo(cx, my - 12);
        ctx.lineTo(cx, my + 14);
        ctx.lineWidth = 3;
        ctx.stroke();
        break;
      case 'o':
        inkShape(ctx, () => { ctx.beginPath(); ctx.ellipse(cx, my, 20, 26, 0, 0, Math.PI * 2); }, '#7a2b34', { lineWidth: 6 });
        break;
      case 'gasp':
        inkShape(ctx, () => { ctx.beginPath(); ctx.ellipse(cx, my, 26, 34, 0, 0, Math.PI * 2); }, '#7a2b34', { lineWidth: 6 });
        break;
      case 'smirk':
        ctx.beginPath();
        ctx.moveTo(cx - mw, my);
        ctx.quadraticCurveTo(cx, my + 26, cx + mw, my - 8);
        ctx.stroke();
        break;
      case 'line':
      default:
        ctx.beginPath();
        ctx.moveTo(cx - mw, my);
        ctx.lineTo(cx + mw, my);
        ctx.stroke();
        break;
    }
  }
  ctx.restore();

  // blush
  if (face.blush > 0.02) {
    for (const side of [-1, 1]) {
      const g = ctx.createRadialGradient(cx + side * 82, eyeY + 46, 2, cx + side * 82, eyeY + 46, 34);
      g.addColorStop(0, `rgba(230,110,120,${0.35 * face.blush})`);
      g.addColorStop(1, 'rgba(230,110,120,0)');
      ctx.fillStyle = g;
      ctx.beginPath();
      ctx.ellipse(cx + side * 82, eyeY + 46, 34, 20, 0, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  ctx.restore(); // head rotation

  // ---- accessories ----
  if (spec.accessory === 'glasses') {
    ctx.save();
    ctx.strokeStyle = INK;
    ctx.lineWidth = 7;
    for (const side of [-1, 1]) {
      ctx.beginPath();
      ctx.arc(cx + side * eyeDX, eyeY, 40, 0, Math.PI * 2);
      ctx.stroke();
    }
    ctx.beginPath();
    ctx.moveTo(cx - eyeDX + 40, eyeY);
    ctx.lineTo(cx + eyeDX - 40, eyeY);
    ctx.stroke();
    ctx.restore();
  } else if (spec.accessory === 'scarf') {
    inkShape(ctx, () => roundRect(ctx, cx - 88, headCY + 108, 176, 44, 18), '#d94f4f', { lineWidth: 7 });
  } else if (spec.accessory === 'staff') {
    capsuleInk(ctx, cx + 150, 300, cx + 150, 960, 9, '#7a5230', 6);
    inkShape(ctx, () => { ctx.beginPath(); ctx.arc(cx + 150, 280, 26, 0, Math.PI * 2); }, '#7fd1c7', { lineWidth: 7 });
  } else if (spec.accessory === 'bag') {
    inkShape(ctx, () => roundRect(ctx, cx + 74, torsoCY + 10, 90, 84, 14), '#8a5a34', { lineWidth: 7 });
  }

  ctx.restore(); // bob / lean
  ctx.restore(); // width scale
}

/* Cached canvases so repeated frames reuse the same object. */
const cardCache = new Map();

export function characterCard(spec, state, key) {
  let entry = cardCache.get(key);
  if (!entry) {
    const canvas = makeCanvas(CARD_W, CARD_H);
    entry = { canvas, ctx: canvas.getContext('2d') };
    cardCache.set(key, entry);
  }
  drawCharacter(entry.ctx, spec, state);
  return entry.canvas;
}

export function clearCardCache() {
  cardCache.clear();
}

/* Utility used by the fallback image generator and the sample builder:
 * a stable, deterministic character from any string seed. */
export function characterFromSeed(seed, name = 'Character') {
  const rnd = mulberry32(hashString(seed));
  const skins = ['#f0c49a', '#e0a97e', '#c98a5e', '#a86b45', '#8a5233', '#f7d6b4'];
  const hairs = ['#2a1a12', '#4a2c18', '#111111', '#7a4a1e', '#b08a4a', '#8a8a8a'];
  const outfits = ['#c0392b', '#2f6fb5', '#4a8c5a', '#8e5bb5', '#d9822b', '#3b7f8c', '#b5476f'];
  const styles = ['tunic', 'coat', 'dress', 'armor', 'apron', 'robe'];
  const hairStyles = ['short', 'long', 'bun', 'spiky', 'curly'];
  const gears = ['none', 'none', 'cap', 'hat', 'crown', 'helmet', 'turban', 'hood'];
  const accs = ['none', 'none', 'none', 'glasses', 'scarf', 'staff', 'bag'];
  const pick = (arr) => arr[Math.floor(rnd() * arr.length)];
  return {
    id: seed,
    name,
    skin: pick(skins),
    hair: pick(hairs),
    hairStyle: pick(hairStyles),
    outfit: pick(outfits),
    outfitStyle: pick(styles),
    pants: pick(['#2f3b52', '#3a3a3a', '#5a4632', '#1f2a3a']),
    shoe: '#3a2a1e',
    headgear: pick(gears),
    accessory: pick(accs),
    height: 1,
    width: 1,
  };
}

export function hashString(s) {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}
