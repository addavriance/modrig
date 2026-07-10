from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import httpx

from app.config import settings

_download_semaphore = asyncio.Semaphore(settings.max_parallel_downloads)
_dest_locks: dict[Path, asyncio.Lock] = {}


def sha1_of(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


async def download_file(client: httpx.AsyncClient, url: str, dest: Path, sha1: str | None = None, retries: int = 3) -> Path:
    """Download url to dest, skipping if dest already exists and matches sha1 (or just exists, if no hash given).
    Retries transient connection failures - large batches of concurrent downloads (asset objects,
    libraries) occasionally hit a reset connection under load.

    Different maven coordinates can resolve to the same destination file (e.g. vanilla and a
    loader both declaring the same library); serialize by destination path so two concurrent
    downloads don't both try to write/rename the same .part file (fails outright on Windows,
    where an open file can't be renamed)."""
    if dest.exists() and (sha1 is None or sha1_of(dest) == sha1):
        return dest

    lock = _dest_locks.setdefault(dest, asyncio.Lock())
    async with lock:
        if dest.exists() and (sha1 is None or sha1_of(dest) == sha1):
            return dest

        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".part")

        for attempt in range(1, retries + 1):
            try:
                async with _download_semaphore:
                    async with client.stream("GET", url, timeout=settings.request_timeout, follow_redirects=True) as resp:
                        resp.raise_for_status()
                        with open(tmp, "wb") as f:
                            async for chunk in resp.aiter_bytes(1 << 16):
                                f.write(chunk)
                break
            except httpx.TransportError:
                if attempt == retries:
                    raise
                await asyncio.sleep(0.5 * attempt)

        tmp.replace(dest)
        return dest


def new_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(headers={"User-Agent": "modrig/0.1 (mod test service)"})
