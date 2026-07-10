from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class Loader(str, Enum):
    fabric = "fabric"
    forge = "forge"
    neoforge = "neoforge"


class InstanceStatus(str, Enum):
    preparing = "preparing"
    downloading = "downloading"
    running = "running"
    crashed = "crashed"
    stopped = "stopped"
    exited = "exited"
    failed = "failed"


class ModSource(str, Enum):
    modrinth = "modrinth"
    local = "local"


class ModRef(BaseModel):
    project_id: str
    version_id: Optional[str] = None
    # source="local" pulls (project_id, version_id) from the locally-published mod
    source: ModSource = ModSource.modrinth # optional


class LocalModInfo(BaseModel):
    mod_id: str
    version: str
    loader: Loader
    mc_version_range: Optional[str] = None
    display_name: Optional[str] = None
    filename: str
    size: Optional[int] = None
    uploaded_at: str


class CreateInstanceRequest(BaseModel):
    mc_version: str
    loader: Loader
    loader_version: Optional[str] = None
    mods: list[ModRef] = Field(default_factory=list)
    ephemeral: bool = True


class InstanceInfo(BaseModel):
    id: str
    mc_version: str
    loader: Loader
    loader_version: Optional[str] = None
    ephemeral: bool
    status: InstanceStatus
    created_at: str
    finished_at: Optional[str] = None
    exit_code: Optional[int] = None
    error: Optional[str] = None
