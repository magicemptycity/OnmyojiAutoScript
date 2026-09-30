"""Shared manual progress transition for normal accounts and fixed-time groups."""
from datetime import datetime
from typing import Any, Callable

from fastapi import HTTPException

from module.config.utils import convert_to_underscore
from module.config.multi_account_task_progress import settle_completed_group


def set_task_progress_status(
    owner: Any, task_name: str, value: str, display_name: Callable[[str], str],
) -> None:
    status = value.strip().lower()
    if status not in {"completed", "failed", "unfinished", "pending"}:
        raise HTTPException(status_code=400, detail="任务状态只能是 completed、failed、unfinished 或 pending")
    key = convert_to_underscore(task_name)
    completed = [name for name in owner.completed_task_names if name != key]
    failed = [name for name in owner.failed_task_names if name != key]
    unfinished = [name for name in owner.unfinished_task_names if name != key]
    if status == "completed":
        completed.append(key)
    elif status == "failed":
        failed.append(key)
    elif status == "unfinished":
        unfinished.append(key)
    elif owner.last_complete_time.date() == datetime.now().date():
        # A manually pending task must not be skipped by today's completion marker.
        owner.last_complete_time = datetime(2023, 1, 1)
    owner.completed_task_list = "\n".join(display_name(name) for name in dict.fromkeys(completed))
    owner.failed_task_list = "\n".join(display_name(name) for name in dict.fromkeys(failed))
    owner.unfinished_task_list = "\n".join(display_name(name) for name in dict.fromkeys(unfinished))
    owner.task_progress_time = datetime.now()
