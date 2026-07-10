from __future__ import annotations

import json
import shutil
import tomllib
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

from app.config import settings
from app.db import get_db
from app.models import ModRef
from app.services.http import sha1_of


@dataclass
class LocalModMetadata:
    mod_id: str
    version: str
    loader: str
    mc_version_range: str | None
    display_name: str | None


def _parse_fabric(zf: zipfile.ZipFile) -> LocalModMetadata:
    data = json.loads(zf.read("fabric.mod.json"))
    mod_id = data.get("id")
    if not mod_id:
        raise ValueError("fabric.mod.json has no 'id'")
    return LocalModMetadata(
        mod_id=mod_id,
        version=str(data.get("version", "0.0.0")),
        loader="fabric",
        mc_version_range=data.get("depends", {}).get("minecraft"),
        display_name=data.get("name"),
    )


def _parse_forge_like(zf: zipfile.ZipFile, toml_path: str, loader: str) -> LocalModMetadata:
    data = tomllib.loads(zf.read(toml_path).decode("utf-8"))
    mods = data.get("mods") or []
    if not mods:
        raise ValueError(f"{toml_path} has no [[mods]] entries")
    mod = mods[0]
    mod_id = mod.get("modId")
    if not mod_id:
        raise ValueError(f"{toml_path}'s [[mods]] entry has no modId")

    mc_range = None
    for dep in data.get("dependencies", {}).get(mod_id, []):
        if dep.get("modId") == "minecraft":
            mc_range = dep.get("versionRange")
            break

    return LocalModMetadata(
        mod_id=mod_id,
        version=str(mod.get("version", "0.0.0")),
        loader=loader,
        mc_version_range=mc_range,
        display_name=mod.get("displayName"),
    )


def parse_mod_jar(content: bytes) -> LocalModMetadata:
    """Extracts mod id / version / target Minecraft version from a Fabric or Forge/NeoForge mod
    jar by reading its own manifest (fabric.mod.json, or META-INF/(neoforge.)mods.toml)."""
    try:
        with zipfile.ZipFile(BytesIO(content)) as zf:
            names = set(zf.namelist())
            if "fabric.mod.json" in names:
                return _parse_fabric(zf)
            if "META-INF/neoforge.mods.toml" in names:
                return _parse_forge_like(zf, "META-INF/neoforge.mods.toml", "neoforge")
            if "META-INF/mods.toml" in names:
                return _parse_forge_like(zf, "META-INF/mods.toml", "forge")
    except zipfile.BadZipFile as exc:
        raise ValueError("Not a valid jar/zip file") from exc

    raise ValueError("Not a recognizable mod jar: no fabric.mod.json or META-INF/mods.toml found")


async def publish(content: bytes, filename: str) -> tuple[LocalModMetadata, bool]:
    """Parses and stores a mod jar under local_mods/<mod_id>/<version>/, replacing any existing
    file for that exact (mod_id, version) pair. Returns (metadata, replaced_existing)."""
    meta = parse_mod_jar(content)

    dest_dir = settings.local_mods_dir / meta.mod_id / meta.version
    replaced = dest_dir.exists()

    if replaced:
        shutil.rmtree(dest_dir)

    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / filename
    dest_path.write_bytes(content)

    db = get_db()
    await db.execute(
        """INSERT OR REPLACE INTO local_mods
               (mod_id, version, loader, mc_version_range, display_name, filename, path, sha1, size, uploaded_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            meta.mod_id,
            meta.version,
            meta.loader,
            meta.mc_version_range,
            meta.display_name,
            filename,
            str(dest_path),
            sha1_of(dest_path),
            len(content),
            datetime.now(timezone.utc).isoformat(),
        ),
    )

    await db.commit()
    return meta, replaced


async def list_local_mods() -> list[dict]:
    db = get_db()
    cur = await db.execute(
        """SELECT mod_id, version, loader, mc_version_range, display_name, filename, size, uploaded_at
           FROM local_mods ORDER BY uploaded_at DESC"""
    )
    rows = await cur.fetchall()
    return [
        {
            "mod_id": r[0],
            "version": r[1],
            "loader": r[2],
            "mc_version_range": r[3],
            "display_name": r[4],
            "filename": r[5],
            "size": r[6],
            "uploaded_at": r[7],
        }
        for r in rows
    ]


async def delete_local_mod(mod_id: str, version: str) -> bool:
    db = get_db()

    cur = await db.execute("SELECT path FROM local_mods WHERE mod_id = ? AND version = ?", (mod_id, version))
    row = await cur.fetchone()

    if row is None:
        return False

    path = Path(row[0])
    if path.parent.exists():
        shutil.rmtree(path.parent)

    await db.execute("DELETE FROM local_mods WHERE mod_id = ? AND version = ?", (mod_id, version))
    await db.commit()

    return True


async def resolve_local_mods(refs: list[ModRef]) -> list[tuple[str, Path]]:
    """Looks up (project_id, version_id) ModRefs with source="local" in the local mod DB."""
    db = get_db()
    results: list[tuple[str, Path]] = []

    for ref in refs:
        if ref.version_id:
            cur = await db.execute(
                "SELECT filename, path FROM local_mods WHERE mod_id = ? AND version = ?",
                (ref.project_id, ref.version_id),
            )
        else:
            cur = await db.execute(
                "SELECT filename, path FROM local_mods WHERE mod_id = ? ORDER BY uploaded_at DESC LIMIT 1",
                (ref.project_id,),
            )
        row = await cur.fetchone()
        if row is None:
            version_hint = f" version {ref.version_id}" if ref.version_id else ""
            raise ValueError(
                f"Local mod '{ref.project_id}'{version_hint} not found - publish it first via POST /mods/local"
            )
        filename, path = row
        results.append((filename, Path(path)))

    return results
