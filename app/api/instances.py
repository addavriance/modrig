from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.models import CreateInstanceRequest, InstanceInfo
from app.services import history
from app.services.instance_pool import pool

router = APIRouter(tags=["instances"])


@router.post("/instances", response_model=InstanceInfo)
async def create_instance(req: CreateInstanceRequest) -> InstanceInfo:
    instance = await pool.create(req)
    return instance.to_info()


@router.get("/instances", response_model=list[InstanceInfo])
async def list_instances() -> list[InstanceInfo]:
    return [i.to_info() for i in pool.list()]


@router.get("/instances/{instance_id}", response_model=InstanceInfo)
async def get_instance(instance_id: str) -> InstanceInfo:
    instance = pool.get(instance_id)
    if instance is None:
        raise HTTPException(status_code=404, detail="instance not found")
    return instance.to_info()


@router.post("/instances/{instance_id}/stop")
async def stop_instance(instance_id: str) -> dict:
    if pool.get(instance_id) is None:
        raise HTTPException(status_code=404, detail="instance not found")
    stopped = await pool.stop(instance_id)
    if not stopped:
        raise HTTPException(status_code=409, detail="instance is not running")
    return {"ok": True}


@router.delete("/instances/{instance_id}")
async def delete_instance(instance_id: str) -> dict:
    if pool.get(instance_id) is None:
        raise HTTPException(status_code=404, detail="instance not found")
    deleted = await pool.delete(instance_id)
    if not deleted:
        raise HTTPException(status_code=409, detail="instance is still active; stop it first")
    return {"ok": True}


@router.get("/instances/{instance_id}/logs")
async def get_logs(instance_id: str, from_line: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=5000)) -> dict:
    # Logs live in the history store, outside the instance dir, so pagination keeps working
    # even after ephemeral cleanup or a manual DELETE removed the instance from the pool.
    if pool.get(instance_id) is None and not history.has_history(instance_id):
        raise HTTPException(status_code=404, detail="instance not found")
    lines = history.read_log(instance_id, from_line=from_line, limit=limit)
    return {"from_line": from_line, "count": len(lines), "lines": lines}


@router.get("/instances/{instance_id}/crash")
async def get_crash(instance_id: str) -> dict:
    instance = pool.get(instance_id)

    if instance is None and not history.has_history(instance_id):
        raise HTTPException(status_code=404, detail="instance not found")

    crash_file = history.crash_path(instance_id)
    crash_text = crash_file.read_text(encoding="utf-8") if crash_file.exists() else None
    tail = history.read_log(instance_id, from_line=max(0, _log_line_count(instance_id) - 200), limit=200)

    return {
        "status": instance.status if instance else None,
        "exit_code": instance.exit_code if instance else None,
        "crash_report": crash_text,
        "log_tail": tail,
    }


def _log_line_count(instance_id: str) -> int:
    path = history.log_path(instance_id)
    if not path.exists():
        return 0
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return sum(1 for _ in f)
