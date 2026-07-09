from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel


class Settings(BaseModel):
    base_dir: Path = Path("data")
    max_concurrent_instances: int = 3

    modrinth_api: str = "https://api.modrinth.com/v2"
    mojang_manifest_url: str = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
    fabric_meta_url: str = "https://meta.fabricmc.net/v2"

    request_timeout: float = 60.0
    max_parallel_downloads: int = 24

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
    def db_path(self) -> Path:
        return self.base_dir / "modrig.db"

    def ensure_dirs(self) -> None:
        for d in (self.cache_dir, self.instances_dir, self.history_dir):
            d.mkdir(parents=True, exist_ok=True)


settings = Settings(base_dir=Path("data").resolve())
