#!/usr/bin/env python3
"""pollinations.py — optional AI image generation via the Pollinations API.

This module is deliberately optional: the video pipeline never depends on it.
If generation is unavailable the renderer keeps going with procedural Canvas
character cards and Three.js environments (see imagegen/fallback.py).

Current API (as documented at gen.pollinations.ai/docs, checked 2026-11):
    GET {base}/image/{prompt}?model=<id>&width=&height=&seed=&enhance=
    base default: https://gen.pollinations.ai
    auth: Authorization: Bearer <key>   (or ?key=<key>)
    model ids: either a catalog id like "black-forest-labs/flux.1-schnell"
               or a short alias like "flux"

Nothing is hardcoded to a dead endpoint: the base URL, model and auth all come
from config.json / environment, and the live catalog is queried when possible.

    python imagegen/pollinations.py --prompt "bean-head girl on a bridge" \
        --out assets/generated/hero.png --model flux
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from typing import Dict, List, Optional
from urllib.parse import quote

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from downloader import DownloadError, download  # noqa: E402

try:
    import requests
except Exception:  # pragma: no cover
    requests = None  # type: ignore

DEFAULT_BASE = "https://gen.pollinations.ai"
DEFAULT_MODEL = "flux"
# short aliases that the API accepts; used if the live catalog cannot be read
KNOWN_ALIASES = ["flux", "zimage", "turbo", "kontext", "gptimage", "klein", "seedream"]


def load_config(path: str = "config.json") -> Dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


class PollinationsClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        verbose: bool = True,
    ) -> None:
        cfg = load_config().get("imagegen", {})
        self.api_key = api_key or os.environ.get("POLLINATIONS_API_KEY", "").strip()
        self.base_url = (base_url or os.environ.get("POLLINATIONS_BASE_URL") or DEFAULT_BASE).rstrip("/")
        self.model = model or os.environ.get("POLLINATIONS_MODEL") or cfg.get("model") or DEFAULT_MODEL
        self.width = int(cfg.get("width", 1024))
        self.height = int(cfg.get("height", 1536))
        self.timeout = int(cfg.get("timeoutSec", 120))
        self.max_attempts = int(cfg.get("maxAttempts", 5))
        self.backoff_base = float(cfg.get("backoffBaseSec", 3))
        self.backoff_max = float(cfg.get("backoffMaxSec", 45))
        self.output_dir = cfg.get("outputDir", "assets/generated")
        self.verbose = verbose

    # ------------------------------------------------------------------ #
    def _log(self, msg: str) -> None:
        if self.verbose:
            print(f"[pollinations] {msg}")

    def _headers(self) -> Dict[str, str]:
        h = {"Accept": "image/*"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def _params(self, extra: Optional[Dict] = None) -> Dict:
        p = {
            "model": self.model,
            "width": self.width,
            "height": self.height,
            "seed": -1,
            "enhance": "false",
            "safe": "false",
        }
        if self.api_key:
            # header is preferred, but the query form is documented too
            pass
        if extra:
            p.update({k: v for k, v in extra.items() if v is not None})
        return p

    # ------------------------------------------------------------------ #
    def list_models(self) -> List[str]:
        """Live catalog (no auth needed). Empty list when offline."""
        if requests is None:
            return []
        try:
            r = requests.get(f"{self.base_url}/image/models", timeout=20)
            if r.status_code != 200:
                return []
            data = r.json()
            items = data if isinstance(data, list) else (data.get("data") or data.get("models") or [])
            out: List[str] = []
            for it in items:
                if isinstance(it, str):
                    out.append(it)
                elif isinstance(it, dict):
                    for key in ("id", "name", "model"):
                        if it.get(key):
                            out.append(str(it[key]))
                            break
                    for alias in (it.get("aliases") or []):
                        out.append(str(alias))
            return sorted(set(out))
        except Exception:
            return []

    def resolve_model(self) -> str:
        """Keep the configured model if the catalog knows it, else pick a safe one."""
        models = self.list_models()
        if not models:
            self._log(f"catalog unreachable, using model '{self.model}'")
            return self.model
        if self.model in models:
            return self.model
        # try a suffix match ("flux" → "black-forest-labs/flux.1-schnell")
        for m in models:
            if self.model in m:
                self._log(f"model '{self.model}' resolved to catalog id '{m}'")
                return m
        for alias in KNOWN_ALIASES:
            if alias in models:
                self._log(f"model '{self.model}' not in catalog; using '{alias}'")
                return alias
        pick = models[0]
        self._log(f"falling back to catalog model '{pick}'")
        return pick

    # ------------------------------------------------------------------ #
    def image_url(self, prompt: str, model: Optional[str] = None, seed: int = -1) -> str:
        params = self._params({"model": model or self.model, "seed": seed})
        qs = "&".join(f"{k}={quote(str(v))}" for k, v in params.items())
        return f"{self.base_url}/image/{quote(prompt, safe='')}?{qs}"

    def generate(self, prompt: str, out_path: Optional[str] = None, seed: int = -1,
                 model: Optional[str] = None) -> str:
        """Generate one image and save it. Returns the saved path.

        Raises DownloadError (fatal=True) on auth/balance problems so the caller
        can stop immediately instead of hammering the service.
        """
        if requests is None:
            raise DownloadError("the 'requests' package is not installed", fatal=True)

        model = model or self.resolve_model()
        if not out_path:
            safe = "".join(c if c.isalnum() else "_" for c in prompt[:40]).strip("_") or "image"
            out_path = os.path.join(self.output_dir, f"{safe}.png")
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)

        url = self.image_url(prompt, model=model, seed=seed)
        self._log(f"GET {url[:160]}{'…' if len(url) > 160 else ''}")
        try:
            path, size = download(
                url,
                out_path,
                headers=self._headers(),
                timeout=self.timeout,
                max_attempts=self.max_attempts,
                backoff_base=self.backoff_base,
                backoff_max=self.backoff_max,
                min_bytes=2048,
                verbose=self.verbose,
            )
            self._log(f"saved {path} ({size/1024:.0f} KB)")
            return path
        except DownloadError as exc:
            if exc.fatal:
                raise
            raise DownloadError(f"image generation failed: {exc}", status=exc.status)

    def available(self) -> bool:
        """Cheap check: can we reach the endpoint at all?"""
        if requests is None:
            return False
        try:
            r = requests.get(f"{self.base_url}/image/models", timeout=15)
            return r.status_code == 200
        except Exception:
            return False


# ---------------------------------------------------------------------- #
# CLI
# ---------------------------------------------------------------------- #


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate an image with Pollinations (optional module)")
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--out", default=None)
    ap.add_argument("--model", default=None)
    ap.add_argument("--seed", type=int, default=-1)
    ap.add_argument("--width", type=int, default=None)
    ap.add_argument("--height", type=int, default=None)
    ap.add_argument("--list-models", action="store_true")
    ap.add_argument("--check", action="store_true", help="only check reachability")
    args = ap.parse_args()

    client = PollinationsClient(model=args.model)
    if args.width:
        client.width = args.width
    if args.height:
        client.height = args.height

    if args.list_models:
        models = client.list_models()
        print(f"{len(models)} models")
        for m in models[:60]:
            print(" ", m)
        return 0

    if args.check:
        print("reachable" if client.available() else "unreachable")
        return 0 if client.available() else 1

    try:
        path = client.generate(args.prompt, args.out, seed=args.seed)
    except DownloadError as exc:
        print(f"[pollinations] FAILED: {exc}", file=sys.stderr)
        return 2 if exc.fatal else 1
    print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
