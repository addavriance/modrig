from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from pydantic import BaseModel, Field


def _discover_java_homes() -> dict[int, Path]:
    """Reads JAVA_HOME_<major> env vars (e.g. JAVA_HOME_17, JAVA_HOME_21) so the right JDK can be
    picked per-instance from the version json's javaVersion.majorVersion."""
    pattern = re.compile(r"^JAVA_HOME_(\d+)$")
    homes: dict[int, Path] = {}
    for name, value in os.environ.items():
        match = pattern.match(name)
        if match and value:
            homes[int(match.group(1))] = Path(value)
    return homes


class Settings(BaseModel):
    base_dir: Path = Path("data")
    max_concurrent_instances: int = 3
    java_homes: dict[int, Path] = Field(default_factory=_discover_java_homes)

    modrinth_api: str = "https://api.modrinth.com/v2"
    mojang_manifest_url: str = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
    fabric_meta_url: str = "https://meta.fabricmc.net/v2"

    forge_maven_url: str = "https://maven.minecraftforge.net"
    forge_promotions_url: str = "https://files.minecraftforge.net/net/minecraftforge/forge/promotions_slim.json"

    neoforge_maven_url: str = "https://maven.neoforged.net/releases"
    neoforge_versions_url: str = "https://maven.neoforged.net/api/maven/versions/releases/net/neoforged/neoforge"

    request_timeout: float = 60.0
    max_parallel_downloads: int = 24
    installer_timeout: float = 900.0

    @property
    def cache_dir(self) -> Path:
        return self.base_dir / "cache"

    @property
    def instances_dir(self) -> Path:
        return self.base_dir / "instances"

    @property
    def history_dir(self) -> Path:
        return self.base_dir / "history"

    @property
    def local_mods_dir(self) -> Path:
        return self.base_dir / "local_mods"

    @property
    def db_path(self) -> Path:
        return self.base_dir / "modrig.db"

    def ensure_dirs(self) -> None:
        for d in (self.cache_dir, self.instances_dir, self.history_dir, self.local_mods_dir):
            d.mkdir(parents=True, exist_ok=True)

    def resolve_java_bin(self, major_version: int | None) -> str:
        """Maps a version json's javaVersion.majorVersion to a configured JDK home
        (JAVA_HOME_<major> env vars); falls back to whatever "java" resolves to on PATH."""
        home = self.java_homes.get(major_version) if major_version is not None else None
        if home is not None:
            exe = home / "bin" / ("java.exe" if sys.platform.startswith("win") else "java")
            if exe.exists():
                return str(exe)
        return "java"


settings = Settings(base_dir=Path("data").resolve())
