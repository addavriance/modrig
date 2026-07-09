from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

import httpx

from app.config import settings
from app.services.http import download_file

# Avoid "There is no minecraft launcher profile in ..." lol
_LAUNCHER_PROFILES_STUB = (
    '{"profiles":{},"selectedProfile":"","clientToken":'
    '"00000000-0000-0000-0000-000000000000","authenticationDatabase":{}}'
)

# The installer writes its own version json well before all of its processors (mojmaps download,
# binary patching) actually finish, so that file existing is not proof of a complete install - a
# run interrupted partway through (e.g. a flaky download inside the installer itself) can leave it
# behind. Only write this marker once the installer has actually exited 0.
_COMPLETE_MARKER = ".modrig_install_complete"


@dataclass
class LoaderResult:
    """What a loader module hands back to the instance pool after resolving/installing itself.

    Forge and NeoForge ship as an installer.jar that produces its own libraries directory
    containing both regular maven artifacts *and* locally binary-patched client jars that have
    no download URL at all - those are only ever discoverable by pointing `library_directory`
    at that installer output and letting BootstrapLauncher scan it itself. Fabric (and vanilla)
    use a flat classpath instead, so `library_directory` stays unused for them.
    """

    profile: dict
    library_directory: Path | None = None
    include_client_jar: bool = True


def merge_with_vanilla(vanilla: dict, child_profile: dict, include_child_libraries: bool = True) -> dict:
    """Merges a loader's launch profile (which declares "inheritsFrom") with its parent vanilla
    version json, the same way the official launcher resolves inheritance chains."""

    merged = dict(vanilla)
    merged["id"] = child_profile.get("id", vanilla.get("id"))
    merged["mainClass"] = child_profile["mainClass"]

    merged_libs = list(vanilla.get("libraries", []))
    if include_child_libraries:
        existing_names = {lib.get("name") for lib in merged_libs}
        for lib in child_profile.get("libraries", []):
            if lib.get("name") not in existing_names:
                merged_libs.append(lib)

    merged["libraries"] = merged_libs

    v_args = vanilla.get("arguments", {})
    c_args = child_profile.get("arguments", {})
    merged["arguments"] = {
        "game": [*v_args.get("game", []), *c_args.get("game", [])],
        "jvm": [*v_args.get("jvm", []), *c_args.get("jvm", [])],
    }
    return merged


async def run_installer(installer_jar: Path, install_dir: Path, java_bin: str = "java") -> None:
    """Runs a Forge/NeoForge installer.jar headlessly against install_dir via --installClient.
    Raises RuntimeError with the installer's own output on failure (e.g. very old Forge versions
    that never supported a headless CLI mode)."""

    install_dir.mkdir(parents=True, exist_ok=True)
    profiles_stub = install_dir / "launcher_profiles.json"

    if not profiles_stub.exists():
        profiles_stub.write_text(_LAUNCHER_PROFILES_STUB, encoding="utf-8")

    process = await asyncio.create_subprocess_exec(
        java_bin, "-jar", str(installer_jar), "--installClient", str(install_dir),
        cwd=install_dir,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )

    assert process.stdout is not None
    output = await process.stdout.read()

    try:
        await asyncio.wait_for(process.wait(), timeout=settings.installer_timeout)
    except asyncio.TimeoutError:
        process.kill()
        raise RuntimeError("Installer timed out") from None

    if process.returncode != 0:
        tail = output.decode("utf-8", errors="replace")[-4000:]
        raise RuntimeError(f"Installer exited with code {process.returncode}:\n{tail}")

    (install_dir / _COMPLETE_MARKER).write_text("", encoding="utf-8")


def find_produced_version(install_dir: Path, exclude_id: str) -> tuple[str, dict]:
    """Locates the loader's own version json under install_dir/versions/, skipping the plain
    vanilla one (named exactly like the Minecraft version) that the installer also writes there."""

    import json

    versions_dir = install_dir / "versions"
    for candidate_dir in sorted(versions_dir.iterdir()) if versions_dir.exists() else []:
        if not candidate_dir.is_dir() or candidate_dir.name == exclude_id:
            continue
        json_path = candidate_dir / f"{candidate_dir.name}.json"
        if json_path.exists():
            return candidate_dir.name, json.loads(json_path.read_text(encoding="utf-8"))

    raise RuntimeError(f"Installer did not produce a version profile under {versions_dir}")


async def ensure_installed(
    client: httpx.AsyncClient, install_dir: Path, installer_url: str, installer_cache_path: Path, mc_version: str
) -> tuple[str, dict]:
    """Returns the loader's produced version (id, json), running the installer only if a previous
    attempt hasn't already completed one successfully (see _COMPLETE_MARKER)."""

    if (install_dir / _COMPLETE_MARKER).exists():
        return find_produced_version(install_dir, exclude_id=mc_version)

    await download_file(client, installer_url, installer_cache_path)
    await run_installer(installer_cache_path, install_dir)
    return find_produced_version(install_dir, exclude_id=mc_version)
