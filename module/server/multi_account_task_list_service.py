"""Task-list behavior shared by normal accounts and fixed-time task groups.

Persisting configuration and refreshing each mode's scheduler remain the caller's job.
"""
import re
from typing import Any, Callable

from fastapi import HTTPException

from module.config.utils import convert_to_underscore


def reorder_enabled_tasks(entries: list[Any], task_names: str) -> list[Any]:
    enabled = [entry for entry in entries if entry.enable]
    requested = [convert_to_underscore(name.strip())
                 for name in re.split(r'[,\n]', task_names) if name.strip()]
    names = [convert_to_underscore(entry.task_name) for entry in enabled]
    if len(requested) != len(names) or len(set(requested)) != len(requested) or set(requested) != set(names):
        raise HTTPException(status_code=400, detail="排序任务必须与当前已启用任务完全一致")
    by_name = {convert_to_underscore(entry.task_name): entry for entry in enabled}
    return [*(by_name[name] for name in requested), *(entry for entry in entries if not entry.enable)]


def add_group_task(batch: Any, task_key: str, task_factory: Callable[..., Any]) -> None:
    entry = batch.task_entry(task_key)
    if entry is None:
        batch.task_list.append(task_factory(task_name=task_key))
    else:
        # Re-adding resumes an existing task without replacing config or progress.
        entry.enable = True


def set_entry_enabled(entry: Any, enable: bool) -> None:
    entry.enable = enable
