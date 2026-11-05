#!/usr/bin/env python3
"""downloader.py — resumable, polite HTTP downloader used by the image module.

Rules baked in here:
  * 502/503/504 → exponential backoff and retry
  * 429        → respect Retry-After
  * 401/403    → stop immediately, it is an auth problem (retrying is pointless)
  * 402        → report insufficient balance/quota and stop
  * no IP rotation, no infinite loops: attempts are capped and then we give up
    so the pipeline can fall back to procedural art
"""

from __future__ import annotations

import os
import random
import time
from typing import Optional, Tuple

try:
    import requests
except Exception:  # pragma: no cover
    requests = None  # type: ignore

RETRY_STATUS = {500, 502, 503, 504}


class DownloadError(RuntimeError):
    def __init__(self, message: str, status: Optional[int] = None, fatal: bool = False):
        super().__init__(message)
        self.status = status
        self.fatal = fatal


def download(
    url: str,
    dest: str,
    *,
    params: Optional[dict] = None,
    headers: Optional[dict] = None,
    timeout: int = 120,
    max_attempts: int = 5,
    backoff_base: float = 3.0,
    backoff_max: float = 45.0,
    resume: bool = True,
    min_bytes: int = 1024,
    verbose: bool = True,
) -> Tuple[str, int]:
    """Download `url` to `dest`. Returns (path, bytes_written)."""
    if requests is None:
        raise DownloadError("the 'requests' package is not installed")

    os.makedirs(os.path.dirname(os.path.abspath(dest)) or ".", exist_ok=True)
    last_error = "unknown error"

    for attempt in range(1, max_attempts + 1):
        existing = os.path.getsize(dest) if (resume and os.path.exists(dest)) else 0
        req_headers = dict(headers or {})
        if existing:
            req_headers["Range"] = f"bytes={existing}-"

        try:
            with requests.get(url, params=params, headers=req_headers, timeout=timeout, stream=True) as resp:
                status = resp.status_code

                if status in (401, 403):
                    raise DownloadError(
                        f"authentication failed ({status}) — check credentials/permissions",
                        status=status, fatal=True,
                    )
                if status == 402:
                    raise DownloadError(
                        "payment required (402) — the provider reports insufficient balance/quota",
                        status=status, fatal=True,
                    )
                if status == 429:
                    wait = _retry_after(resp, default=min(backoff_max, backoff_base * attempt * 2))
                    last_error = "429 rate limited"
                    if verbose:
                        print(f"[download] 429 rate limited, waiting {wait:.0f}s (attempt {attempt}/{max_attempts})")
                    time.sleep(wait)
                    continue
                if status in RETRY_STATUS:
                    wait = min(backoff_max, (backoff_base * (2 ** (attempt - 1))) + random.uniform(0, 1.2))
                    last_error = f"{status}"
                    if verbose:
                        print(f"[download] {status}, retrying in {wait:.0f}s (attempt {attempt}/{max_attempts})")
                    time.sleep(wait)
                    continue
                if status not in (200, 206):
                    last_error = f"HTTP {status}"
                    if verbose:
                        print(f"[download] unexpected HTTP {status} (attempt {attempt}/{max_attempts})")
                    time.sleep(min(backoff_max, backoff_base * attempt))
                    continue

                mode = "ab" if (status == 206 and existing) else "wb"
                written = 0
                with open(dest, mode) as f:
                    for chunk in resp.iter_content(chunk_size=65536):
                        if chunk:
                            f.write(chunk)
                            written += len(chunk)

                size = os.path.getsize(dest)
                if size < min_bytes:
                    last_error = f"file too small ({size} bytes)"
                    if verbose:
                        print(f"[download] {last_error}, retrying")
                    time.sleep(min(backoff_max, backoff_base * attempt))
                    continue
                if verbose:
                    print(f"[download] {dest} ({size/1024:.0f} KB)")
                return dest, size

        except DownloadError:
            raise
        except Exception as exc:
            last_error = str(exc)
            if verbose:
                print(f"[download] error: {exc} (attempt {attempt}/{max_attempts})")
            time.sleep(min(backoff_max, backoff_base * attempt))

    raise DownloadError(f"download failed after {max_attempts} attempts: {last_error}")


def _retry_after(resp, default: float = 10.0) -> float:
    try:
        v = resp.headers.get("Retry-After")
        if v:
            return max(1.0, min(120.0, float(v)))
    except Exception:
        pass
    return default
