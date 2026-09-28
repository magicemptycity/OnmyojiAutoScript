"""Shared runner task-local progress transitions for timed and orchestration singles.

Account/group completion lists and scheduler next-run remain mode-owned.
"""
from datetime import datetime
from typing import Any

def set_task_local_status(entry: Any, status: str, *, now: datetime | None = None) -> None:
    """Update only task-local progress; never touch scheduling or configuration."""
    if status not in {"completed", "failed", "running"}:
        raise ValueError(f"Invalid task status: {status}")
    stamp = now or datetime.now()
    entry.status = status
    entry.task_progress_time = stamp
    if status == "completed":
        entry.last_complete_time = stamp


def task_local_status_today(entry: Any, *, now: datetime | None = None) -> str:
    stamp = now or datetime.now()
    return entry.status if entry.task_progress_time.date() == stamp.date() else "pending"
