from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
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
    no download URL at all. Which bootstrap a given profile uses isn't a simple "older/newer MC
    version" story - Forge and NeoForge forked apart at 1.20.1 and evolve their bootstraps on
    separate, out-of-sync timelines. BootstrapLauncher-based profiles find those local-only jars
    themselves by scanning `library_directory` (set via -DlibraryDirectory); "classpath-only"
    bootstraps (e.g. Forge 1.20.6+'s ForgeBootstrap) don't scan anything - they just read
    java.class.path - so those same jars have to be added to -cp explicitly via
    `extra_classpath_jars` instead. Fabric (and vanilla) use a flat classpath and need neither.
    """

    profile: dict
    library_directory: Path | None = None
    include_client_jar: bool = True
    extra_classpath_jars: list[Path] = field(default_factory=list)


def uses_module_path(child_profile: dict) -> bool:
    """Whether this loader's own jvm args include a literal -p (module path) argument. If they
    don't, this is one of the "classpath-only" bootstraps (e.g. Forge 1.20.6+'s ForgeBootstrap) -
    not necessarily a newer Minecraft version, just a different bootstrap lineage (see
    LoaderResult's docstring)."""
    return any(item == "-p" for item in child_profile.get("arguments", {}).get("jvm", []) if isinstance(item, str))


def local_only_library_paths(child_profile: dict, install_dir: Path) -> list[Path]:
    """Resolves declared libraries that have no download URL (installer-generated patched jars,
    e.g. "net.minecraftforge:forge:<v>:client") to their actual location under
    install_dir/libraries, for bootstraps that need them listed on the classpath directly rather
    than discovering them via -DlibraryDirectory (see uses_module_path)."""

    paths = []
    for lib in child_profile.get("libraries", []):
        artifact = lib.get("downloads", {}).get("artifact")
        if artifact and artifact.get("path") and not artifact.get("url"):
            candidate = install_dir / "libraries" / artifact["path"]
            if candidate.exists():
                paths.append(candidate)
    return paths


def _module_path_artifact_keys(child_profile: dict) -> set[str]:
    """Extracts "<group-path>/<artifactId>" keys (version-agnostic!) for every jar referenced by
    the loader's own literal -p (module path) JVM argument, by parsing out the
    ${library_directory}/<group-path>/<artifactId>/<version>/<file>.jar structure and dropping the
    version/filename segments.

    Any library matching one of these - whether declared by vanilla or by the loader itself -
    must not *also* land on the classpath: the JVM module system rejects two modules sharing a
    name regardless of version, so even a *different* version of the same artifact collides."""

    keys: set[str] = set()
    take_next = False

    for item in child_profile.get("arguments", {}).get("jvm", []):
        if not isinstance(item, str):
            take_next = False
            continue

        if take_next:
            for part in item.split("${classpath_separator}"):
                path = part.split("${library_directory}/", 1)[-1]
                segments = path.split("/")

                if len(segments) >= 3:
                    keys.add("/".join(segments[:-2]))  # drop <version>/<file>.jar

            take_next = False
        elif item == "-p":
            take_next = True
    return keys


def _artifact_key(lib: dict) -> str | None:
    """"<group-path>/<artifactId>" for a library's own maven coordinate, e.g.
    "org.ow2.asm:asm:9.6" -> "org/ow2/asm/asm" - matches the format _module_path_artifact_keys
    extracts from -p, deliberately ignoring version/classifier."""

    name = lib.get("name")
    if not name:
        return None

    parts = name.split(":")
    if len(parts) < 2:
        return None

    return "/".join([*parts[0].split("."), parts[1]])


def merge_with_vanilla(vanilla: dict, child_profile: dict, include_child_libraries: bool = True) -> dict:
    """Merges a loader's launch profile (which declares "inheritsFrom") with its parent vanilla
    version json, the same way the official launcher resolves inheritance chains."""

    merged = dict(vanilla)
    merged["id"] = child_profile.get("id", vanilla.get("id"))
    merged["mainClass"] = child_profile["mainClass"]

    module_path_keys = _module_path_artifact_keys(child_profile)

    merged_libs = [lib for lib in vanilla.get("libraries", []) if _artifact_key(lib) not in module_path_keys]
    if include_child_libraries:
        existing_names = {lib.get("name") for lib in merged_libs}
        for lib in child_profile.get("libraries", []):
            if lib.get("name") in existing_names:
                continue
            if _artifact_key(lib) in module_path_keys:
                continue
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
    """Locates the loader's version.json under install_dir/versions/, skipping the vanilla one.

    Directory names aren't reliable because the installer may resolve the requested Minecraft
    version to a different concrete release. Instead, we identify the loader profile by the
    presence of "inheritsFrom"; name-based exclusion is only a fallback."""

    import json

    versions_dir = install_dir / "versions"
    candidates = []

    for candidate_dir in sorted(versions_dir.iterdir()) if versions_dir.exists() else []:
        if not candidate_dir.is_dir():
            continue

        json_path = candidate_dir / f"{candidate_dir.name}.json"
        if json_path.exists():
            candidates.append((candidate_dir.name, json.loads(json_path.read_text(encoding="utf-8"))))

    for name, data in candidates:
        if "inheritsFrom" in data:
            return name, data
    for name, data in candidates:
        if name != exclude_id:
            return name, data

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
