from __future__ import annotations

from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from app.api.cache import router as cache_router
from app.api.instances import router as instances_router
from app.config import settings
from app.db import close_db, init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.ensure_dirs()
    await init_db()
    yield
    await close_db()


app = FastAPI(title="modrig", description="Autonomous Minecraft mod test server", lifespan=lifespan)
app.include_router(instances_router)
app.include_router(cache_router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
