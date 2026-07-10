from __future__ import annotations

import asyncio
import sys
import zipfile
from pathlib import Path
from typing import Any

import httpx

from app.config import settings
from app.services.cache import register_cache_entry
from app.services.http import download_file

if sys.platform.startswith("win"):
    CURRENT_OS = "windows"
elif sys.platform == "darwin":
    CURRENT_OS = "osx"
else:
    CURRENT_OS = "linux"


async def get_version_manifest(client: httpx.AsyncClient) -> dict:
    r = await client.get(settings.mojang_manifest_url, timeout=settings.request_timeout)
    r.raise_for_status()
    return r.json()


async def get_version_json(client: httpx.AsyncClient, mc_version: str) -> dict:
    manifest = await get_version_manifest(client)
    entry = next((v for v in manifest["versions"] if v["id"] == mc_version), None)
    if entry is None:
        raise ValueError(f"Unknown Minecraft version: {mc_version}")
    r = await client.get(entry["url"], timeout=settings.request_timeout)
    r.raise_for_status()
    return r.json()


def rule_permits(rules: list[dict] | None, os_name: str = CURRENT_OS, features: dict | None = None) -> bool:
    """Last matching rule wins; default deny if any rules are present (mirrors the official launcher)."""
    if not rules:
        return True
    features = features or {}
    result = False
    for rule in rules:
        allow = rule.get("action") == "allow"
        matches = True
        os_rule = rule.get("os")
        if os_rule and os_rule.get("name") and os_rule.get("name") != os_name:
            matches = False
        feat_rule = rule.get("features")
        if feat_rule:
            for fk, fv in feat_rule.items():
                if features.get(fk) != fv:
                    matches = False
        if matches:
            result = allow
    return result


def maven_coord_to_path(coord: str) -> str:
    parts = coord.split(":")
    group, artifact, version = parts[0], parts[1], parts[2]
    classifier = parts[3] if len(parts) > 3 else None
    filename = f"{artifact}-{version}" + (f"-{classifier}" if classifier else "") + ".jar"
    return "/".join([*group.split("."), artifact, version, filename])


def resolve_library_artifact(lib: dict) -> tuple[str, str, str | None] | None:
    """Returns (relative_path, download_url, sha1) for a library's main artifact, or None if disallowed."""
    if not rule_permits(lib.get("rules")):
        return None
    downloads = lib.get("downloads")
    if downloads and downloads.get("artifact"):
        artifact = downloads["artifact"]
        return artifact["path"], artifact["url"], artifact.get("sha1")
    if lib.get("url"):
        path = maven_coord_to_path(lib["name"])
        base = lib["url"] if lib["url"].endswith("/") else lib["url"] + "/"
        return path, base + path, lib.get("sha1")
    return None


def resolve_library_natives(lib: dict) -> tuple[str, str, str | None] | None:
    """Returns (relative_path, download_url, sha1) for a library's natives classifier for CURRENT_OS, if any."""
    if not rule_permits(lib.get("rules")):
        return None
    natives_map = lib.get("natives")
    downloads = lib.get("downloads")
    if not natives_map or not downloads:
        return None
    classifier_key = natives_map.get(CURRENT_OS)
    if not classifier_key:
        return None
    classifier_key = classifier_key.replace("${arch}", "64")
    classifier = downloads.get("classifiers", {}).get(classifier_key)
    if not classifier:
        return None
    return classifier["path"], classifier["url"], classifier.get("sha1")


def get_java_major_version(version_json: dict) -> int | None:
    return version_json.get("javaVersion", {}).get("majorVersion")


async def download_client_jar(client: httpx.AsyncClient, version_json: dict, mc_version: str) -> Path:
    dest = settings.cache_dir / "versions" / mc_version / "client.jar"
    download = version_json["downloads"]["client"]
    await download_file(client, download["url"], dest, download.get("sha1"))
    await register_cache_entry(
        "version", mc_version, dest, download.get("sha1"), download.get("size"),
        java_major_version=get_java_major_version(version_json),
    )
    return dest


async def download_libraries(client: httpx.AsyncClient, libraries: list[dict]) -> tuple[list[Path], list[Path]]:
    """Downloads regular libraries (classpath jars) and native-classifier jars into the shared cache.
    Returns (classpath_jar_paths, native_jar_paths)."""
    classpath: list[Path] = []
    native_jars: list[Path] = []
    lib_cache = settings.cache_dir / "libraries"

    async def handle(lib: dict) -> None:
        artifact = resolve_library_artifact(lib)
        if artifact:
            rel_path, url, sha1 = artifact
            dest = lib_cache / rel_path
            await download_file(client, url, dest, sha1)
            classpath.append(dest)
        natives = resolve_library_natives(lib)
        if natives:
            rel_path, url, sha1 = natives
            dest = lib_cache / rel_path
            await download_file(client, url, dest, sha1)
            native_jars.append(dest)

    await asyncio.gather(*(handle(lib) for lib in libraries))
    return classpath, native_jars


def extract_natives(native_jars: list[Path], mc_version: str) -> Path:
    natives_dir = settings.cache_dir / "natives" / mc_version
    marker = natives_dir / ".extracted"

    if marker.exists():
        return natives_dir

    natives_dir.mkdir(parents=True, exist_ok=True)

    for jar in native_jars:
        with zipfile.ZipFile(jar) as zf:
            for member in zf.namelist():
                if member.startswith("META-INF/") or member.endswith("/"):
                    continue
                zf.extract(member, natives_dir)
    marker.touch()
    return natives_dir


async def download_assets(client: httpx.AsyncClient, version_json: dict) -> Path:
    asset_index_ref = version_json["assetIndex"]
    index_id = asset_index_ref["id"]
    index_path = settings.cache_dir / "assets" / "indexes" / f"{index_id}.json"

    await download_file(client, asset_index_ref["url"], index_path, asset_index_ref.get("sha1"))
    await register_cache_entry("asset_index", index_id, index_path, asset_index_ref.get("sha1"))

    import json

    index = json.loads(index_path.read_text(encoding="utf-8"))
    objects_dir = settings.cache_dir / "assets" / "objects"

    async def fetch_object(obj_hash: str) -> None:
        prefix = obj_hash[:2]
        dest = objects_dir / prefix / obj_hash
        url = f"https://resources.download.minecraft.net/{prefix}/{obj_hash}"
        await download_file(client, url, dest, obj_hash)

    hashes = {obj["hash"] for obj in index["objects"].values()}
    await asyncio.gather(*(fetch_object(h) for h in hashes))
    return settings.cache_dir / "assets"
