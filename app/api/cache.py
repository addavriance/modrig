from __future__ import annotations

from fastapi import APIRouter

from app.services.cache import list_cache_entries

router = APIRouter(tags=["cache"])


@router.get("/cache/versions")
async def cache_versions() -> list[dict]:
    return await list_cache_entries(["version", "loader_profile", "asset_index"])


@router.get("/cache/mods")
async def cache_mods() -> list[dict]:
    return await list_cache_entries(["mod"])
