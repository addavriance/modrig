from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.services import local_mods

router = APIRouter(tags=["mods"])


@router.post("/mods/local")
async def publish_local_mod(file: UploadFile = File(...)) -> dict:
    if not file.filename or not file.filename.lower().endswith(".jar"):
        raise HTTPException(status_code=400, detail="expected a .jar file")

    content = await file.read()
    try:
        meta, replaced = await local_mods.publish(content, file.filename)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return {
        "mod_id": meta.mod_id,
        "version": meta.version,
        "loader": meta.loader,
        "mc_version_range": meta.mc_version_range,
        "display_name": meta.display_name,
        "replaced": replaced,
    }


@router.get("/mods/local")
async def list_local_mods() -> list[dict]:
    return await local_mods.list_local_mods()


@router.delete("/mods/local/{mod_id}/{version}")
async def delete_local_mod(mod_id: str, version: str) -> dict:
    deleted = await local_mods.delete_local_mod(mod_id, version)
    if not deleted:
        raise HTTPException(status_code=404, detail="local mod not found")
    return {"ok": True}
