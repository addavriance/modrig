from __future__ import annotations

import httpx

from app.config import settings
from app.services.cache import register_cache_entry
from app.services.loaders.common import (
    LoaderResult,
    ensure_installed,
    local_only_library_paths,
    merge_with_vanilla,
    uses_module_path,
)


async def _get_promotions(client: httpx.AsyncClient) -> dict:
    r = await client.get(settings.forge_promotions_url, timeout=settings.request_timeout)
    r.raise_for_status()
    return r.json()["promos"]


async def resolve_loader_version(client: httpx.AsyncClient, mc_version: str, loader_version: str | None) -> str:
    if loader_version:
        return loader_version

    promos = await _get_promotions(client)
    for key in (f"{mc_version}-recommended", f"{mc_version}-latest"):
        if key in promos:
            return promos[key]
    raise ValueError(f"No Forge build available for Minecraft {mc_version}")


def _installer_url(mc_version: str, forge_version: str) -> str:
    coord = f"{mc_version}-{forge_version}"
    return f"{settings.forge_maven_url}/net/minecraftforge/forge/{coord}/forge-{coord}-installer.jar"


async def prepare(client: httpx.AsyncClient, vanilla_json: dict, mc_version: str, loader_version: str) -> LoaderResult:
    """Forge ships as a GUI/CLI installer.jar rather than a plain launch-profile JSON: running
    it headlessly (--installClient) produces both a merge-able version json *and* a libraries
    directory containing locally binary-patched client jars that have no download URL at all."""

    install_dir = settings.cache_dir / "forge_installs" / f"{mc_version}-{loader_version}"
    installer_path = settings.cache_dir / "installers" / f"forge-{mc_version}-{loader_version}-installer.jar"
    version_id, forge_profile = await ensure_installed(
        client, install_dir, _installer_url(mc_version, loader_version), installer_path, mc_version
    )
    # Declared libraries go on the classpath like Fabric's; the undeclared patched client jars
    # are found by BootstrapLauncher itself via -DlibraryDirectory. Adding the whole install dir
    # instead (incl. installer-only tools like ForgeAutoRenamingTool) causes JPMS split-package
    # errors, so we don't.
    profile = merge_with_vanilla(vanilla_json, forge_profile, include_child_libraries=True)

    await register_cache_entry(
        "loader_profile",
        f"forge:{mc_version}:{loader_version}",
        install_dir / "versions" / version_id / f"{version_id}.json",
    )

    # Newer "classpath-only" bootstraps (e.g. ForgeBootstrap on 1.20.6+) don't scan
    # -DlibraryDirectory themselves, so the locally-patched client jar has to go on -cp directly.
    extra_classpath_jars = [] if uses_module_path(forge_profile) else local_only_library_paths(forge_profile, install_dir)

    return LoaderResult(
        profile=profile,
        library_directory=install_dir / "libraries",
        include_client_jar=False,
        extra_classpath_jars=extra_classpath_jars,
    )
