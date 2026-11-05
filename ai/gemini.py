#!/usr/bin/env python3
"""gemini.py — minimal Google Gemini REST client (no SDK, no build step).

The API key is read from the environment (GEMINI_API_KEY / GOOGLE_API_KEY),
which in GitHub Actions comes from a repository secret. It is never written to
disk, never logged, and never sent to the frontend.

    from gemini import GeminiClient
    client = GeminiClient()
    data = client.generate_json("...prompt...")
"""

from __future__ import annotations

import json
import os
import random
import re
import time
from typing import Any, Dict, List, Optional

try:
    import requests
except Exception:  # pragma: no cover
    requests = None  # type: ignore

API_ROOT = "https://generativelanguage.googleapis.com/v1beta/models"

DEFAULT_MODELS = [
    "gemini-flash-lite-latest",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash-lite",
]

RETRY_STATUS = {429, 500, 502, 503, 504}


class GeminiError(RuntimeError):
    """Raised for auth / quota problems that a retry cannot fix."""


def load_config(path: str = "config.json") -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


class GeminiClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        models: Optional[List[str]] = None,
        temperature: float = 0.9,
        max_output_tokens: int = 4096,
        timeout: int = 120,
        max_attempts: int = 4,
        verbose: bool = True,
    ) -> None:
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or ""
        cfg = load_config().get("ai", {})
        env_model = os.environ.get("GEMINI_MODEL", "").strip()
        self.models = [m for m in ([env_model] if env_model else []) + (models or cfg.get("models") or DEFAULT_MODELS) if m]
        self.temperature = float(cfg.get("temperature", temperature))
        self.max_output_tokens = int(cfg.get("maxOutputTokens", max_output_tokens))
        self.timeout = timeout
        self.max_attempts = max_attempts
        self.verbose = verbose
        self.last_error: Optional[str] = None

    # ------------------------------------------------------------------ #
    @property
    def available(self) -> bool:
        return bool(self.api_key) and requests is not None

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(f"[gemini] {msg}")

    # ------------------------------------------------------------------ #
    def generate_text(self, prompt: str, system: str = "", temperature: Optional[float] = None) -> str:
        """Plain text generation. Tries each configured model in order."""
        if not self.available:
            raise GeminiError("GEMINI_API_KEY is not set (add it as a repository secret)")

        body: Dict[str, Any] = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": self.temperature if temperature is None else temperature,
                "maxOutputTokens": self.max_output_tokens,
                "topP": 0.95,
            },
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}

        last_exc: Optional[Exception] = None
        for model in self.models:
            url = f"{API_ROOT}/{model}:generateContent"
            for attempt in range(1, self.max_attempts + 1):
                try:
                    resp = requests.post(
                        url,
                        params={"key": self.api_key},
                        json=body,
                        timeout=self.timeout,
                        headers={"Content-Type": "application/json"},
                    )
                except Exception as exc:
                    last_exc = exc
                    self._log(f"{model}: network error ({exc}); attempt {attempt}")
                    time.sleep(min(30, 2 ** attempt))
                    continue

                status = resp.status_code
                if status == 200:
                    data = resp.json()
                    text = _extract_text(data)
                    if text:
                        self._log(f"{model}: ok ({len(text)} chars)")
                        return text
                    last_exc = GeminiError("empty response")
                    break

                if status in (401, 403):
                    self.last_error = f"authentication failed ({status})"
                    raise GeminiError(
                        f"Gemini authentication failed ({status}). Check the GEMINI_API_KEY secret."
                    )
                if status == 400:
                    detail = _short(resp)
                    # bad model name for this key → try the next model
                    self._log(f"{model}: 400 {detail}")
                    last_exc = GeminiError(f"400: {detail}")
                    break
                if status == 404:
                    self._log(f"{model}: not available for this key, trying next model")
                    last_exc = GeminiError(f"404 model {model}")
                    break
                if status == 429:
                    wait = _retry_after(resp, default=8 * attempt)
                    self._log(f"{model}: rate limited, waiting {wait:.0f}s")
                    time.sleep(wait)
                    last_exc = GeminiError("429 rate limited")
                    continue
                if status in RETRY_STATUS:
                    wait = min(45, (2 ** attempt) + random.uniform(0, 1.5))
                    self._log(f"{model}: {status}, retrying in {wait:.0f}s")
                    time.sleep(wait)
                    last_exc = GeminiError(f"{status}")
                    continue

                self.last_error = _short(resp)
                raise GeminiError(f"Gemini API error {status}: {self.last_error}")

        raise GeminiError(f"all Gemini models failed: {last_exc}")

    # ------------------------------------------------------------------ #
    def generate_json(self, prompt: str, system: str = "", temperature: Optional[float] = None) -> Any:
        """Text generation that must return JSON. Repairs fences and trailing prose."""
        text = self.generate_text(prompt, system=system, temperature=temperature)
        return parse_json_loose(text)


# ---------------------------------------------------------------------- #
# helpers
# ---------------------------------------------------------------------- #


def _extract_text(data: Dict[str, Any]) -> str:
    try:
        cands = data.get("candidates") or []
        if not cands:
            return ""
        parts = cands[0].get("content", {}).get("parts", [])
        return "".join(p.get("text", "") for p in parts).strip()
    except Exception:
        return ""


def _short(resp) -> str:
    try:
        j = resp.json()
        return str(j.get("error", {}).get("message", ""))[:300]
    except Exception:
        return (resp.text or "")[:300]


def _retry_after(resp, default: float = 10.0) -> float:
    try:
        v = resp.headers.get("Retry-After")
        if v:
            return max(1.0, float(v))
    except Exception:
        pass
    return default


def parse_json_loose(text: str) -> Any:
    """Parse JSON out of a model reply that may be fenced or chatty."""
    if not text:
        raise GeminiError("empty model output")
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    try:
        return json.loads(t)
    except Exception:
        pass
    # first {...} or [...] block
    for opener, closer in (("{", "}"), ("[", "]")):
        start = t.find(opener)
        end = t.rfind(closer)
        if start != -1 and end > start:
            chunk = t[start:end + 1]
            try:
                return json.loads(chunk)
            except Exception:
                # strip trailing commas, a very common model slip
                try:
                    return json.loads(re.sub(r",\s*([}\]])", r"\1", chunk))
                except Exception:
                    continue
    raise GeminiError(f"could not parse JSON from model output: {t[:200]}")


if __name__ == "__main__":  # tiny self-check
    c = GeminiClient()
    print(f"available={c.available} models={c.models}")
    if c.available:
        print(c.generate_text("Reply with exactly: ok")[:200])
