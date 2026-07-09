from __future__ import annotations

import httpx

from app.config import settings
from app.services.cache import register_cache_entry


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


def merge_with_vanilla(vanilla: dict, fabric_profile: dict) -> dict:
    """Merges a Fabric launch profile (which uses "inheritsFrom") with its parent vanilla version json,
    the same way the official launcher resolves inheritance chains."""

    merged = dict(vanilla)
    merged["id"] = fabric_profile.get("id", vanilla.get("id"))
    merged["mainClass"] = fabric_profile["mainClass"]

    existing_names = {lib.get("name") for lib in vanilla.get("libraries", [])}
    merged_libs = list(vanilla.get("libraries", []))

    for lib in fabric_profile.get("libraries", []):
        if lib.get("name") not in existing_names:
            merged_libs.append(lib)

    merged["libraries"] = merged_libs

    v_args = vanilla.get("arguments", {})
    f_args = fabric_profile.get("arguments", {})

    merged["arguments"] = {
        "game": [*v_args.get("game", []), *f_args.get("game", [])],
        "jvm": [*v_args.get("jvm", []), *f_args.get("jvm", [])],
    }
    return merged
