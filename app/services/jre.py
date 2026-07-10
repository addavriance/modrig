from __future__ import annotations

import asyncio
import platform
import sys
from pathlib import Path

import httpx

from app.config import settings
from app.services.cache import register_cache_entry
from app.services.http import download_file

# Mojang's own managed-runtime index
_RUNTIME_INDEX_URL = "https://piston-meta.mojang.com/v1/products/java-runtime/2ec0cc96c44e5a76b9c8b7c39df7210883d12871/all.json"

_COMPLETE_MARKER = ".modrig_runtime_complete"


def _os_key() -> str:
    if sys.platform.startswith("win"):
        return "windows-arm64" if platform.machine().lower() in ("arm64", "aarch64") else "windows-x64"
    if sys.platform == "darwin":
        return "mac-os-arm64" if platform.machine().lower() in ("arm64", "aarch64") else "mac-os"
    return "linux"


async def _find_runtime_entry(client: httpx.AsyncClient, component: str) -> tuple[str, str] | None:
    """Returns (manifest_url, version_name) for `component` (e.g. "java-runtime-delta") on the
    current OS, or None if Mojang doesn't publish one (very old/very new components sometimes
    aren't available for every platform)."""

    r = await client.get(_RUNTIME_INDEX_URL, timeout=settings.request_timeout)
    r.raise_for_status()
    entries = r.json().get(_os_key(), {}).get(component, [])

    if not entries:
        return None

    entry = entries[0]
    return entry["manifest"]["url"], entry["version"]["name"]


async def ensure_runtime(client: httpx.AsyncClient, component: str) -> Path:
    """Downloads Mojang's own managed JRE for `component` into the shared cache, mirroring exactly
    what the official launcher does - so users don't have to hunt down and configure a matching
    system JDK themselves for every Minecraft version's Java requirement."""

    runtime_dir = settings.cache_dir / "runtimes" / component
    java_bin = runtime_dir / "bin" / ("java.exe" if sys.platform.startswith("win") else "java")

    if (runtime_dir / _COMPLETE_MARKER).exists() and java_bin.exists():
        return java_bin

    found = await _find_runtime_entry(client, component)
    if found is None:
        raise ValueError(f"Mojang has no managed runtime for {component!r} on {_os_key()}")

    manifest_url, version_name = found

    r = await client.get(manifest_url, timeout=settings.request_timeout)
    r.raise_for_status()
    files: dict[str, dict] = r.json()["files"]

    async def handle_entry(rel_path: str, entry: dict) -> None:
        entry_type = entry.get("type")
        dest = runtime_dir / rel_path
        if entry_type == "file":
            download = entry["downloads"]["raw"]
            await download_file(client, download["url"], dest, download.get("sha1"))
            if entry.get("executable"):
                try:
                    dest.chmod(dest.stat().st_mode | 0o111)
                except OSError:
                    pass
        elif entry_type == "link":
            target = entry.get("target")
            if not target:
                return
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists() or dest.is_symlink():
                dest.unlink()
            try:
                dest.symlink_to(target)
            except OSError:
                pass
        # "directory" entries need no action - download_file() creates parent dirs itself.

    await asyncio.gather(*(handle_entry(rel_path, entry) for rel_path, entry in files.items()))

    if not java_bin.exists():
        raise RuntimeError(f"Managed runtime {component} ({version_name}) did not produce {java_bin}")

    (runtime_dir / _COMPLETE_MARKER).write_text("", encoding="utf-8")
    await register_cache_entry("runtime", component, java_bin)

    return java_bin
