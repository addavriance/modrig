from __future__ import annotations

import httpx

from app.config import settings
from app.services.cache import register_cache_entry
from app.services.loaders.common import LoaderResult, ensure_installed, merge_with_vanilla


def _mc_version_prefix(mc_version: str) -> str:
    """NeoForge versions are numbered "<mc_minor>.<mc_patch>.<build>" (e.g. Minecraft 1.20.4 ->
    NeoForge 20.4.x; 1.21 -> 21.0.x since the patch component defaults to 0)."""

    parts = mc_version.split(".")

    if len(parts) < 2 or parts[0] != "1":
        raise ValueError(f"Can't map Minecraft version {mc_version!r} to a NeoForge version prefix")

    minor = parts[1]
    patch = parts[2] if len(parts) > 2 else "0"

    return f"{minor}.{patch}."


def _sort_key(version: str) -> tuple:
    core = version.split("-")[0]
    is_stable = "-beta" not in version

    return (is_stable, tuple(int(p) for p in core.split(".")))


async def _get_versions(client: httpx.AsyncClient) -> list[str]:
    r = await client.get(settings.neoforge_versions_url, timeout=settings.request_timeout)
    r.raise_for_status()

    return r.json()["versions"]


async def resolve_loader_version(client: httpx.AsyncClient, mc_version: str, loader_version: str | None) -> str:
    if loader_version:
        return loader_version

    prefix = _mc_version_prefix(mc_version)
    versions = await _get_versions(client)
    matching = [v for v in versions if v.startswith(prefix)]

    if not matching:
        raise ValueError(f"No NeoForge build available for Minecraft {mc_version}")
    return max(matching, key=_sort_key)


def _installer_url(loader_version: str) -> str:
    return f"{settings.neoforge_maven_url}/net/neoforged/neoforge/{loader_version}/neoforge-{loader_version}-installer.jar"


async def prepare(client: httpx.AsyncClient, vanilla_json: dict, mc_version: str, loader_version: str) -> LoaderResult:
    """NeoForge is a fork of Forge and reuses the same installer.jar mechanism - see forge.py's
    prepare() for why a plain classpath merge isn't enough for BootstrapLauncher-based loaders."""

    install_dir = settings.cache_dir / "neoforge_installs" / f"{mc_version}-{loader_version}"
    installer_path = settings.cache_dir / "installers" / f"neoforge-{loader_version}-installer.jar"
    version_id, neoforge_profile = await ensure_installed(
        client, install_dir, _installer_url(loader_version), installer_path, mc_version
    )
    # See forge.py's prepare() for why declared libraries go on the classpath but the rest of the
    # install dir (patched client jars, installer-only tooling) deliberately doesn't.
    profile = merge_with_vanilla(vanilla_json, neoforge_profile, include_child_libraries=True)

    await register_cache_entry(
        "loader_profile",
        f"neoforge:{mc_version}:{loader_version}",
        install_dir / "versions" / version_id / f"{version_id}.json",
    )

    return LoaderResult(
        profile=profile,
        library_directory=install_dir / "libraries",
        include_client_jar=False,
    )
