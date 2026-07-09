from __future__ import annotations

import httpx

from app.config import settings
from app.services.cache import register_cache_entry
from app.services.loaders.common import LoaderResult, merge_with_vanilla


async def get_loader_versions(client: httpx.AsyncClient, mc_version: str) -> list[dict]:
    r = await client.get(f"{settings.fabric_meta_url}/versions/loader/{mc_version}", timeout=settings.request_timeout)
    r.raise_for_status()
    return r.json()


async def resolve_loader_version(client: httpx.AsyncClient, mc_version: str, loader_version: str | None) -> str:
    versions = await get_loader_versions(client, mc_version)
    if not versions:
        raise ValueError(f"No Fabric loader available for Minecraft {mc_version}")

    if loader_version:
        if not any(v["loader"]["version"] == loader_version for v in versions):
            raise ValueError(f"Fabric loader {loader_version} not found for Minecraft {mc_version}")
        return loader_version

    # Meta API lists versions newest-first; first stable entry is the recommended default.
    stable = next((v for v in versions if v["loader"].get("stable")), versions[0])
    return stable["loader"]["version"]


async def get_profile_json(client: httpx.AsyncClient, mc_version: str, loader_version: str) -> dict:
    url = f"{settings.fabric_meta_url}/versions/loader/{mc_version}/{loader_version}/profile/json"

    r = await client.get(url, timeout=settings.request_timeout)
    r.raise_for_status()
    profile = r.json()

    profile_path = settings.cache_dir / "loader_profiles" / f"fabric-{mc_version}-{loader_version}.json"
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text(r.text, encoding="utf-8")

    await register_cache_entry(
        "loader_profile",
        f"fabric:{mc_version}:{loader_version}",
        profile_path,
        size=len(r.content),
    )
    return profile


async def prepare(client: httpx.AsyncClient, vanilla_json: dict, mc_version: str, loader_version: str) -> LoaderResult:
    """Fabric has no installer step: its launch profile is just a plain JSON describing an
    ordinary flat classpath (fabric-loader/intermediary/asm/mixin), so it merges directly into
    the vanilla profile and needs no library_directory or extra jar discovery."""

    fabric_profile = await get_profile_json(client, mc_version, loader_version)
    profile = merge_with_vanilla(vanilla_json, fabric_profile, include_child_libraries=True)

    return LoaderResult(profile=profile)
