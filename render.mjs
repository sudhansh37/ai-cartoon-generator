#!/usr/bin/env node
// render.mjs — frame-by-frame renderer.
//
// Boots a headless Chromium with CPU WebGL (SwiftShader), loads web/index.html,
// and calls window.renderFrame(i) for every frame, saving each frame as a PNG.
//
//   node render.mjs --plan work/plan.json --out frames
//   node render.mjs --plan work/plan.json --out frames --range 0-59
//   node render.mjs --plan work/plan.json --stills 8 --stills-dir work/stills
//
// Guarantees:
//   * resume      — frames already on disk are skipped unless --force is passed
//   * progress    — a line every N frames with ETA, plus work/render_state.json
//   * error log   — everything also goes to work/render.log

import http from 'node:http';
import fs from 'node:fs';
import fsp from 'node:fs/promises';
import path from 'node:path';
import url from 'node:url';
import { chromium } from 'playwright';

const ROOT = path.resolve(path.dirname(url.fileURLToPath(import.meta.url)));

/* ------------------------------- CLI ------------------------------- */
function parseArgs(argv) {
  const args = {
    plan: 'work/plan.json',
    out: 'frames',
    width: null,
    height: null,
    fps: null,
    range: null,
    stills: 0,
    stillsDir: 'work/stills',
    resume: true,
    force: false,
    serve: false,
    port: 0,
    quality: 1,
    logEvery: 10,
    timeout: 180000,
  };
  for (let i = 2; i < argv.length; i++) {
    const a = argv[i];
    const next = () => argv[++i];
    switch (a) {
      case '--plan': args.plan = next(); break;
      case '--out': args.out = next(); break;
      case '--width': args.width = Number(next()); break;
      case '--height': args.height = Number(next()); break;
      case '--fps': args.fps = Number(next()); break;
      case '--range': args.range = next(); break;
      case '--stills': args.stills = Number(next()); break;
      case '--stills-dir': args.stillsDir = next(); break;
      case '--no-resume': args.resume = false; break;
      case '--force': args.force = true; args.resume = false; break;
      case '--serve': args.serve = true; break;
      case '--port': args.port = Number(next()); break;
      case '--quality': args.quality = Number(next()); break;
      case '--log-every': args.logEvery = Number(next()); break;
      case '--help': case '-h': printHelp(); process.exit(0); break;
      default:
        if (a.startsWith('--')) {
          console.error(`unknown option: ${a}`);
          process.exit(2);
        }
    }
  }
  return args;
}

function printHelp() {
  console.log(`render.mjs — AI Cartoon Generator frame renderer

Options:
  --plan <file>        shot plan JSON (default work/plan.json)
  --out <dir>          frame output dir (default frames)
  --range <a-b>        render only frames a..b (inclusive)
  --stills <n>         render n evenly spaced stills instead of the full video
  --stills-dir <dir>   where to put stills (default work/stills)
  --no-resume          re-render frames that already exist
  --force              same as --no-resume
  --serve              keep the page open (preview/debug)
  --port <n>           static server port (default: random free port)
  --quality <n>        canvas resolution multiplier (default 1.0)
  --log-every <n>      progress line every n frames (default 10)
`);
}

/* --------------------------- logging ------------------------------- */
const LOG_PATH = path.join(ROOT, 'work', 'render.log');
let logStream = null;
function openLog() {
  fs.mkdirSync(path.dirname(LOG_PATH), { recursive: true });
  logStream = fs.createWriteStream(LOG_PATH, { flags: 'a' });
}
function log(msg) {
  const line = `[${new Date().toISOString()}] ${msg}`;
  console.log(msg);
  if (logStream) logStream.write(line + '\n');
}

/* --------------------------- static server -------------------------- */
const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.css': 'text/css; charset=utf-8',
  '.wasm': 'application/wasm',
  '.wav': 'audio/wav',
  '.mp3': 'audio/mpeg',
  '.svg': 'image/svg+xml',
};

function startServer(port = 0) {
  const server = http.createServer(async (req, res) => {
    try {
      const parsed = new URL(req.url, 'http://127.0.0.1');
      let filePath = decodeURIComponent(parsed.pathname);
      if (filePath === '/') filePath = '/web/index.html';
      const abs = path.join(ROOT, path.normalize(filePath).replace(/^(\.\.[/\\])+/, ''));
      if (!abs.startsWith(ROOT)) {
        res.writeHead(403).end('forbidden');
        return;
      }
      const data = await fsp.readFile(abs);
      res.writeHead(200, {
        'Content-Type': MIME[path.extname(abs).toLowerCase()] || 'application/octet-stream',
        'Cache-Control': 'no-store',
        'Cross-Origin-Opener-Policy': 'same-origin',
        'Cross-Origin-Embedder-Policy': 'require-corp',
      });
      res.end(data);
    } catch (err) {
      res.writeHead(404, { 'Content-Type': 'text/plain' }).end('not found: ' + req.url);
    }
  });
  return new Promise((resolve) => {
    server.listen(port, '127.0.0.1', () => resolve({ server, port: server.address().port }));
  });
}

/* ----------------------------- main -------------------------------- */
const CHROMIUM_FLAGS = [
  // CPU WebGL — required on GitHub Actions runners (no GPU)
  '--use-angle=swiftshader',
  '--enable-unsafe-swiftshader',
  '--ignore-gpu-blocklist',
  '--use-gl=angle',
  '--enable-webgl',
  '--disable-dev-shm-usage',
  '--no-sandbox',
  '--disable-setuid-sandbox',
  '--font-render-hinting=none',
  '--force-color-profile=srgb',
];

function frameName(i) {
  return String(i).padStart(6, '0') + '.png';
}

function parseRange(range, total) {
  if (!range) return [0, total - 1];
  const m = String(range).match(/^(\d+)\s*-\s*(\d+)$/);
  if (!m) throw new Error(`bad --range "${range}", expected "a-b"`);
  return [Math.max(0, Number(m[1])), Math.min(total - 1, Number(m[2]))];
}

async function main() {
  const args = parseArgs(process.argv);
  openLog();

  const planPath = path.resolve(ROOT, args.plan);
  if (!fs.existsSync(planPath)) {
    log(`ERROR plan not found: ${planPath}`);
    process.exit(1);
  }
  const plan = JSON.parse(await fsp.readFile(planPath, 'utf8'));
  const fps = args.fps || plan.fps || 30;
  const width = args.width || plan.width || 1080;
  const height = args.height || plan.height || 1920;
  const totalFrames = plan.totalFrames || Math.max(1, Math.round((plan.duration || 1) * fps));

  const outDir = path.resolve(ROOT, args.out);
  await fsp.mkdir(outDir, { recursive: true });

  const { server, port } = await startServer(args.port);
  log(`static server on http://127.0.0.1:${port} (root ${ROOT})`);

  const browser = await chromium.launch({ headless: true, args: CHROMIUM_FLAGS });
  const context = await browser.newContext({
    viewport: { width, height },
    deviceScaleFactor: args.quality,
  });
  const page = await context.newPage();
  page.on('console', (m) => {
    const text = m.text();
    if (m.type() === 'error' || m.type() === 'warning') log(`page ${m.type()}: ${text}`);
  });
  page.on('pageerror', (e) => log(`pageerror: ${e.message}`));

  const planUrlPath = '/' + path.relative(ROOT, planPath).split(path.sep).join('/');
  const target = `http://127.0.0.1:${port}/web/index.html?plan=${encodeURIComponent(planUrlPath)}`;
  log(`opening ${target}`);
  await page.goto(target, { waitUntil: 'load', timeout: args.timeout });

  // wait for the engine to boot (or fail loudly)
  const bootResult = await page
    .waitForFunction(() => window.__ready === true || window.__error, null, { timeout: args.timeout })
    .then(() => page.evaluate(() => ({ ready: window.__ready === true, error: window.__error || null, meta: window.__meta || null })))
    .catch((e) => ({ ready: false, error: 'boot timeout: ' + e.message, meta: null }));

  if (!bootResult.ready) {
    log(`ERROR engine failed to boot: ${bootResult.error}`);
    await browser.close();
    server.close();
    process.exit(1);
  }
  const meta = bootResult.meta || {};
  log(`engine ready: ${meta.frames} frames, ${meta.shots} shots, ${meta.width}x${meta.height} @ ${meta.fps}fps`);
  if (meta.problems && meta.problems.length) {
    log(`plan warnings: ${meta.problems.join(' | ')}`);
  }

  if (args.serve) {
    log('--serve mode: page is live, press Ctrl+C to stop');
    await new Promise(() => {});
  }

  /* ---- stills mode ---- */
  if (args.stills > 0) {
    const dir = path.resolve(ROOT, args.stillsDir);
    await fsp.mkdir(dir, { recursive: true });
    const step = Math.max(1, Math.floor(totalFrames / args.stills));
    const picked = [];
    for (let i = 0; i < args.stills; i++) picked.push(Math.min(totalFrames - 1, i * step));
    log(`rendering ${picked.length} stills into ${dir}`);
    for (const frame of picked) {
      const started = Date.now();
      const dataUrl = await grabFrame(page, frame);
      const file = path.join(dir, `still_${frameName(frame)}`);
      await fsp.writeFile(file, Buffer.from(dataUrl.split(',')[1], 'base64'));
      log(`still ${frame} -> ${path.relative(ROOT, file)} (${Date.now() - started} ms)`);
    }
    await browser.close();
    server.close();
    return;
  }

  /* ---- full render ---- */
  const [from, to] = parseRange(args.range, totalFrames);
  const started = Date.now();
  let rendered = 0;
  let skipped = 0;
  let failed = 0;

  for (let frame = from; frame <= to; frame++) {
    const file = path.join(outDir, frameName(frame));
    if (args.resume && !args.force && fs.existsSync(file) && fs.statSync(file).size > 1000) {
      skipped++;
      continue;
    }
    try {
      const dataUrl = await grabFrame(page, frame);
      await fsp.writeFile(file, Buffer.from(dataUrl.split(',')[1], 'base64'));
      rendered++;
    } catch (err) {
      failed++;
      log(`ERROR frame ${frame}: ${err && err.message}`);
      // one retry with a fresh page — a single bad frame must not kill the run
      try {
        await page.reload({ waitUntil: 'load' });
        await page.waitForFunction(() => window.__ready === true || window.__error, null, { timeout: args.timeout });
        const dataUrl = await grabFrame(page, frame);
        await fsp.writeFile(file, Buffer.from(dataUrl.split(',')[1], 'base64'));
        rendered++;
        failed--;
        log(`recovered frame ${frame} after reload`);
      } catch (err2) {
        log(`ERROR frame ${frame} retry failed: ${err2 && err2.message}`);
      }
    }

    const done = frame - from + 1;
    const span = to - from + 1;
    if (done % args.logEvery === 0 || frame === to) {
      const elapsed = (Date.now() - started) / 1000;
      const perFrame = elapsed / Math.max(1, rendered + skipped);
      const remaining = Math.max(0, span - done) * perFrame;
      const pct = ((done / span) * 100).toFixed(1);
      log(
        `frame ${frame}/${to} (${pct}%) rendered=${rendered} skipped=${skipped} failed=${failed} ` +
        `| ${perFrame.toFixed(2)} s/frame | ETA ${formatEta(remaining)}`
      );
      await fsp.writeFile(
        path.join(ROOT, 'work', 'render_state.json'),
        JSON.stringify(
          {
            plan: path.relative(ROOT, planPath),
            from, to, frame,
            rendered, skipped, failed,
            perFrameSec: Number(perFrame.toFixed(3)),
            etaSec: Math.round(remaining),
            updatedAt: new Date().toISOString(),
          },
          null,
          2
        )
      );
    }
  }

  const total = (Date.now() - started) / 1000;
  log(`render finished in ${total.toFixed(1)}s — rendered=${rendered} skipped=${skipped} failed=${failed}`);
  await browser.close();
  server.close();
  if (failed > 0) process.exitCode = 1;
}

async function grabFrame(page, frame) {
  return page.evaluate((f) => {
    if (window.__error) throw new Error(window.__error);
    window.renderFrame(f);
    const canvas = document.getElementById('stage');
    return canvas.toDataURL('image/png');
  }, frame);
}

function formatEta(sec) {
  if (!isFinite(sec) || sec < 0) return '?';
  const m = Math.floor(sec / 60);
  const s = Math.round(sec % 60);
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
}

main().catch((err) => {
  log(`FATAL ${err && (err.stack || err.message || err)}`);
  process.exit(1);
});
