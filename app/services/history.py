from __future__ import annotations

from pathlib import Path

from app.config import settings


def history_dir(instance_id: str) -> Path:
    d = settings.history_dir / instance_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def has_history(instance_id: str) -> bool:
    return (settings.history_dir / instance_id).exists()


def log_path(instance_id: str) -> Path:
    return history_dir(instance_id) / "run.log"


def crash_path(instance_id: str) -> Path:
    return history_dir(instance_id) / "crash.txt"


def read_log(instance_id: str, from_line: int = 0, limit: int = 200) -> list[str]:
    path = log_path(instance_id)
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    return [line.rstrip("\n") for line in lines[from_line : from_line + limit]]


def find_crash_reports(instance_dir: Path) -> list[Path]:
    reports = list((instance_dir / "crash-reports").glob("crash-*.txt")) if (instance_dir / "crash-reports").exists() else []
    hs_err = list(instance_dir.glob("hs_err_pid*.log"))
    return sorted(reports + hs_err, key=lambda p: p.stat().st_mtime, reverse=True)


def collect_crash_text(instance_dir: Path) -> str | None:
    reports = find_crash_reports(instance_dir)
    if not reports:
        return None

    parts = []
    for report in reports:
        parts.append(f"=== {report.name} ===\n{report.read_text(encoding='utf-8', errors='replace')}")
    return "\n\n".join(parts)
