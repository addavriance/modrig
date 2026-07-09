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


class ModRef(BaseModel):
    project_id: str
    version_id: Optional[str] = None


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
