from __future__ import annotations

import json
from pathlib import Path

import httpx

from app.config import settings
from app.models import ModRef
from app.services.cache import register_cache_entry
from app.services.http import download_file, sha1_of


async def list_versions(client: httpx.AsyncClient, project: str, loaders: list[str], game_versions: list[str]) -> list[dict]:
    params = {"loaders": json.dumps(loaders), "game_versions": json.dumps(game_versions)}
    r = await client.get(f"{settings.modrinth_api}/project/{project}/version", params=params, timeout=settings.request_timeout)
    r.raise_for_status()
    return r.json()


async def get_version(client: httpx.AsyncClient, version_id: str) -> dict:
    r = await client.get(f"{settings.modrinth_api}/version/{version_id}", timeout=settings.request_timeout)
    r.raise_for_status()
    return r.json()


async def resolve_mods(
    client: httpx.AsyncClient, mods: list[ModRef], mc_version: str, loader: str
) -> list[dict]:
    """Resolves requested mods plus their required dependencies into a deduplicated list of
    Modrinth "version" objects to download, one per project."""
    resolved: dict[str, dict] = {}

    async def resolve_one(project: str, version_id: str | None) -> None:
        if version_id:
            version = await get_version(client, version_id)
        else:
            versions = await list_versions(client, project, [loader], [mc_version])
            if not versions:
                raise ValueError(f"No version of '{project}' compatible with {loader} {mc_version}")
            version = versions[0]

        project_id = version["project_id"]
        if project_id in resolved:
            return
        resolved[project_id] = version

        for dep in version.get("dependencies", []):
            if dep.get("dependency_type") != "required":
                continue
            dep_project = dep.get("project_id")
            dep_version = dep.get("version_id")
            if dep_project is None and dep_version:
                dep_full = await get_version(client, dep_version)
                dep_project = dep_full["project_id"]
            if dep_project and dep_project not in resolved:
                await resolve_one(dep_project, dep_version)

    for mod in mods:
        await resolve_one(mod.project_id, mod.version_id)

    return list(resolved.values())


def _primary_file(version: dict) -> dict:
    files = version["files"]
    return next((f for f in files if f.get("primary")), files[0])


async def download_mod_files(client: httpx.AsyncClient, versions: list[dict]) -> list[tuple[str, Path]]:
    """Downloads each resolved mod's primary file into the shared cache, keyed by sha1.
    Returns list of (filename, cached_path)."""
    results: list[tuple[str, Path]] = []
    for version in versions:
        file_info = _primary_file(version)
        file_hash = file_info["hashes"]["sha1"]
        dest = settings.cache_dir / "mods" / file_hash[:2] / f"{file_hash}-{file_info['filename']}"
        await download_file(client, file_info["url"], dest, file_hash)
        await register_cache_entry("mod", file_hash, dest, file_hash, file_info.get("size"))
        results.append((file_info["filename"], dest))
    return results
