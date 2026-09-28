"""Shared validation and task-list edits for the five task-based account modes.

The caller owns persistence and scheduler side effects. Fixed/group mode's legacy
account task list is intentionally different from normal/timed activation.
"""
from typing import Any, Callable

from fastapi import HTTPException

from module.config.utils import convert_to_underscore
from module.server.main_manager import mm
from module.server.multi_account_independent_scheduler import set_private_scheduler_enabled
from module.server.multi_account_router_support import is_multi_account_task
from module.server.multi_account_task_config_service import has_task_script


def validate_task_name(script_name: str, task_name: str) -> str:
    key = convert_to_underscore(task_name.strip())
    if not key or getattr(mm.config_cache(script_name).model, key, None) is None:
        raise HTTPException(status_code=400, detail="任务不存在")
    if is_multi_account_task(key):
        raise HTTPException(status_code=400, detail="不能嵌套多账号多任务")
    if not has_task_script(key):
        raise HTTPException(status_code=400, detail="该功能没有可执行任务，不能添加到多账号任务")
    return key


def add_task_entry(account: Any, key: str, task_factory: Callable[..., Any], *, mode: str) -> bool:
    """Return whether persistence is needed; preserve each mode's duplicate semantics."""
    existing = next((item for item in account.task_list
                     if convert_to_underscore(item.task_name) == key), None)
    if mode == "group":
        if existing is not None:
            return False
        account.task_list.append(task_factory(task_name=key))
    elif mode == "timed":
        if existing is not None:
            set_private_scheduler_enabled(existing.private_config, True)
        else:
            account.task_list.append(task_factory(
                task_name=key, private_config={"scheduler": {"enable": True}}))
    else:
        if existing is not None:
            existing.enable = True
        else:
            account.task_list.append(task_factory(task_name=key, enable=True))
    return True


def delete_task_entry(account: Any, entry: Any, *, mode: str) -> None:
    if mode == "group":
        account.task_list.remove(entry)
    elif mode == "timed":
        set_private_scheduler_enabled(entry.private_config, False)
    else:
        entry.enable = False
