from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings
from app.db import get_db
from app.models import CreateInstanceRequest, InstanceInfo, InstanceStatus, Loader, ModSource
from app.services import history, jre, launch, local_mods, mojang, modrinth
from app.services.auth import build_offline_profile
from app.services.http import new_client
from app.services.loaders import fabric, forge, neoforge

_LOADER_MODULES = {
    Loader.fabric: fabric,
    Loader.forge: forge,
    Loader.neoforge: neoforge,
}

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _detect_java_major_version(java_bin: str) -> int | None:
    """Runs `<java_bin> -version` and parses its major version. Used to catch a required-vs-actual
    JDK mismatch *before* spawning the real game process - otherwise it only surfaces as a cryptic
    "Unrecognized option" (or similar) deep in the JVM's own startup failure, once resolve_java_bin
    has already silently fallen back to a too-old default "java" for lack of a matching JAVA_HOME_<N>."""
    try:
        process = await asyncio.create_subprocess_exec(
            java_bin, "-version", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )
        output, _ = await process.communicate()
    except FileNotFoundError:
        return None

    match = re.search(r'version "(\d+)(?:\.(\d+))?', output.decode("utf-8", errors="replace"))
    if not match:
        return None
    major = int(match.group(1))
    return int(match.group(2)) if major == 1 and match.group(2) else major  # legacy "1.8.0_..." style


@dataclass
class RuntimeInstance:
    id: str
    request: CreateInstanceRequest
    status: InstanceStatus = InstanceStatus.preparing
    created_at: str = field(default_factory=_now)
    finished_at: str | None = None
    exit_code: int | None = None
    error: str | None = None
    resolved_loader_version: str | None = None
    process: asyncio.subprocess.Process | None = None
    expected_stop: bool = False

    @property
    def dir(self) -> Path:
        return settings.instances_dir / self.id

    def to_info(self) -> InstanceInfo:
        return InstanceInfo(
            id=self.id,
            mc_version=self.request.mc_version,
            loader=self.request.loader,
            loader_version=self.resolved_loader_version or self.request.loader_version,
            ephemeral=self.request.ephemeral,
            status=self.status,
            created_at=self.created_at,
            finished_at=self.finished_at,
            exit_code=self.exit_code,
            error=self.error,
        )


class InstancePool:
    def __init__(self, max_concurrent: int) -> None:
        self._instances: dict[str, RuntimeInstance] = {}
        self._semaphore = asyncio.Semaphore(max_concurrent)

    def get(self, instance_id: str) -> RuntimeInstance | None:
        return self._instances.get(instance_id)

    def list(self) -> list[RuntimeInstance]:
        return sorted(self._instances.values(), key=lambda i: i.created_at, reverse=True)

    async def create(self, req: CreateInstanceRequest) -> RuntimeInstance:
        instance = RuntimeInstance(id=str(uuid.uuid4()), request=req)
        self._instances[instance.id] = instance
        await self._persist(instance)

        asyncio.create_task(self._run(instance.id))
        return instance

    async def stop(self, instance_id: str) -> bool:
        instance = self.get(instance_id)
        if instance is None or instance.process is None:
            return False
        instance.expected_stop = True
        try:
            instance.process.terminate()
        except ProcessLookupError:
            pass
        return True

    async def delete(self, instance_id: str) -> bool:
        instance = self.get(instance_id)
        if instance is None:
            return False
        if instance.status in (InstanceStatus.preparing, InstanceStatus.downloading, InstanceStatus.running):
            return False
        if instance.dir.exists():
            shutil.rmtree(instance.dir, ignore_errors=True)
        del self._instances[instance_id]
        return True

    async def _persist(self, instance: RuntimeInstance) -> None:
        db = get_db()
        await db.execute(
            """INSERT INTO runs (id, mc_version, loader, loader_version, mods_json, ephemeral, status,
                                  created_at, finished_at, exit_code, error)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                   loader_version=excluded.loader_version, status=excluded.status,
                   finished_at=excluded.finished_at, exit_code=excluded.exit_code, error=excluded.error""",
            (
                instance.id,
                instance.request.mc_version,
                instance.request.loader.value,
                instance.resolved_loader_version or instance.request.loader_version,
                json.dumps([m.model_dump() for m in instance.request.mods]),
                int(instance.request.ephemeral),
                instance.status.value,
                instance.created_at,
                instance.finished_at,
                instance.exit_code,
                instance.error,
            ),
        )
        await db.commit()

    async def _set_status(self, instance: RuntimeInstance, status: InstanceStatus, **fields) -> None:
        instance.status = status
        for key, value in fields.items():
            setattr(instance, key, value)
        await self._persist(instance)

    async def _run(self, instance_id: str) -> None:
        instance = self._instances[instance_id]
        try:
            await self._prepare_and_launch(instance)
        except Exception as exc:  # noqa: BLE001 - surface any failure on the instance itself
            logger.exception("Instance %s failed", instance_id)
            message = str(exc) or f"{type(exc).__name__} (see server log for the traceback)"
            await self._set_status(instance, InstanceStatus.failed, error=message, finished_at=_now())

    async def _prepare_and_launch(self, instance: RuntimeInstance) -> None:
        req = instance.request
        mc_version = req.mc_version
        loader_module = _LOADER_MODULES[req.loader]

        await self._set_status(instance, InstanceStatus.downloading)

        async with new_client() as client:
            loader_version = await loader_module.resolve_loader_version(client, mc_version, req.loader_version)
            instance.resolved_loader_version = loader_version
            await self._persist(instance)

            vanilla_json = await mojang.get_version_json(client, mc_version)
            required_java = mojang.get_java_major_version(vanilla_json)
            java_component = mojang.get_java_component(vanilla_json)

            # JAVA_HOME_<N> (if configured) always wins; otherwise fetch Mojang's own managed
            # runtime for this version - same thing the official launcher does - instead of
            # requiring the user to hunt down and install a matching system JDK themselves.
            java_bin = settings.java_home_override(required_java)
            if java_bin is None and java_component:
                try:
                    java_bin = str(await jre.ensure_runtime(client, java_component))
                except Exception:
                    logger.exception("Failed to fetch managed runtime %s, falling back to PATH java", java_component)
            if java_bin is None:
                java_bin = "java"

            if required_java is not None:
                detected_java = await _detect_java_major_version(java_bin)
                if detected_java is not None and detected_java < required_java:
                    raise RuntimeError(
                        f"Minecraft {mc_version} needs Java {required_java}+, but '{java_bin}' resolves to "
                        f"Java {detected_java}. Set JAVA_HOME_{required_java} to a Java {required_java}+ "
                        f"install and restart modrig."
                    )

            loader_result = await loader_module.prepare(client, vanilla_json, mc_version, loader_version)
            profile = loader_result.profile

            client_jar = await mojang.download_client_jar(client, vanilla_json, mc_version)
            classpath_jars, native_jars = await mojang.download_libraries(client, profile["libraries"])

            # BootstrapLauncher (Forge/NeoForge) turns every -cp entry into a candidate module and
            # aborts if the same path appears twice (e.g. vanilla and the loader can both declare
            # the same artifact under slightly different maven coordinate spellings).
            classpath_jars = list(dict.fromkeys(classpath_jars))
            natives_dir = mojang.extract_natives(native_jars, mc_version)
            assets_dir = await mojang.download_assets(client, vanilla_json)

            modrinth_refs = [m for m in req.mods if m.source == ModSource.modrinth]
            local_refs = [m for m in req.mods if m.source == ModSource.local]

            mod_files: list[tuple[str, Path]] = []

            if modrinth_refs:
                mod_versions = await modrinth.resolve_mods(client, modrinth_refs, mc_version, req.loader.value)
                mod_files += await modrinth.download_mod_files(client, mod_versions)
            if local_refs:
                mod_files += await local_mods.resolve_local_mods(local_refs)

            await self._launch(
                instance, profile, mc_version, client_jar, classpath_jars, natives_dir, assets_dir, mod_files,
                library_directory=loader_result.library_directory or (settings.cache_dir / "libraries"),
                include_client_jar=loader_result.include_client_jar,
                java_bin=java_bin,
            )

    async def _launch(
        self,
        instance: RuntimeInstance,
        profile: dict,
        mc_version: str,
        client_jar,
        classpath_jars,
        natives_dir,
        assets_dir,
        mod_files,
        library_directory,
        java_bin: str,
        include_client_jar: bool,
    ) -> None:
        instance_dir = instance.dir
        mods_dir = instance_dir / "mods"

        for sub in ("mods", "saves", "config"):
            (instance_dir / sub).mkdir(parents=True, exist_ok=True)

        for filename, cached_path in mod_files:
            shutil.copy2(cached_path, mods_dir / filename)

        auth = build_offline_profile(username=f"Tester{instance.id[:8]}")

        command = launch.build_command(
            profile=profile,
            mc_version=mc_version,
            instance_dir=instance_dir,
            natives_dir=natives_dir,
            assets_dir=assets_dir,
            classpath_jars=classpath_jars,
            client_jar=client_jar,
            auth=auth,
            java_bin=java_bin,
            library_directory=library_directory,
            include_client_jar=include_client_jar,
        )

        log_file_path = history.log_path(instance.id)
        log_file_path.parent.mkdir(parents=True, exist_ok=True)

        async with self._semaphore:
            await self._set_status(instance, InstanceStatus.running)
            with open(log_file_path, "wb") as log_file:
                log_file.write(f"[modrig] launch command: {command!r}\n\n".encode("utf-8"))
                log_file.flush()

                process = await asyncio.create_subprocess_exec(
                    *command,
                    cwd=instance_dir,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )

                instance.process = process

                async def pump_output() -> None:
                    assert process.stdout is not None
                    async for line in process.stdout:
                        log_file.write(line)
                        log_file.flush()

                await asyncio.gather(pump_output(), process.wait())

            await self._finalize(instance)

    async def _finalize(self, instance: RuntimeInstance) -> None:
        process = instance.process
        exit_code = process.returncode if process else None

        crash_text = history.collect_crash_text(instance.dir)
        if crash_text:
            history.crash_path(instance.id).write_text(crash_text, encoding="utf-8")

        if instance.expected_stop:
            # A supervisor-initiated stop kills the JVM via terminate()/SIGTERM, which almost never
            # yields exit code 0 (Java has no window to cleanly click "quit" on) - what matters for
            # crash-vs-stop classification is *who* ended the process, not its raw exit code.
            status = InstanceStatus.stopped
        elif exit_code == 0:
            status = InstanceStatus.exited
        else:
            status = InstanceStatus.crashed

        await self._set_status(instance, status, exit_code=exit_code, finished_at=_now())

        if instance.request.ephemeral and instance.dir.exists():
            shutil.rmtree(instance.dir, ignore_errors=True)


pool = InstancePool(max_concurrent=settings.max_concurrent_instances)
