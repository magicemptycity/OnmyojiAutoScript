"""Common fixed-time group adapter; outer scheduler and persistence stay mode-specific."""
import re
from datetime import datetime
from typing import Any, Callable

from fastapi import HTTPException

from module.config.utils import convert_to_underscore
from module.server.multi_account_router_support import is_multi_account_task
from module.server.multi_account_task_config_service import has_task_script


def normalize_group_task_names(model: Any, task_names: str, group_label: str) -> list[str]:
    result: list[str] = []
    for raw_name in re.split(r"[,\n]", task_names or ""):
        key = convert_to_underscore(raw_name.strip())
        if not key:
            continue
        if is_multi_account_task(key):
            raise HTTPException(status_code=400, detail=f"不能在{group_label}中嵌套多账号任务")
        if getattr(model, key, None) is None or not has_task_script(key):
            raise HTTPException(status_code=400, detail=f"任务不可执行：{raw_name.strip()}")
        if key not in result:
            result.append(key)
    return result


def find_group(account: Any, group_id: str, missing_detail: str) -> Any:
    for group in account.fixed_time_batch_list:
        if group.batch_id == group_id:
            return group
    raise HTTPException(status_code=404, detail=missing_detail)


def require_group_task(group: Any, task_name: str, missing_detail: str) -> Any:
    task = group.task_entry(task_name)
    if task is None:
        raise HTTPException(status_code=404, detail=missing_detail)
    return task


def ensure_disabled_group_task(group: Any, task_name: str, factory: Callable[..., Any]) -> Any:
    task = group.task_entry(task_name)
    if task is None:
        task = factory(task_name=convert_to_underscore(task_name.strip()), enable=False)
        group.task_list.append(task)
    return task


def serialize_group(account: Any, group: Any, *, display_name: Callable[[str], str],
                    next_run_for: Callable[[Any, Any, datetime], datetime | None],
                    default_name: str, include_item_type: bool = False) -> dict:
    now = datetime.now()
    today = group.task_progress_time.date() == now.date()
    completed = set(group.completed_task_names) if today else set()
    failed = set(group.failed_task_names) if today else set()
    unfinished = set(group.unfinished_task_names) if today else set()
    next_run = next_run_for(account, group, now)
    scheduler = group.scheduler
    data = {
        "batch_id": group.batch_id,
        "name": group.name or default_name,
        "enable": scheduler.enable,
        "run_time": scheduler.server_update.strftime("%H:%M"),
        "schedule_mode": getattr(scheduler.schedule_mode, "value", scheduler.schedule_mode),
        "interval_days": scheduler.delay_date,
        "weekdays": scheduler.weekdays,
        "random_week_days": scheduler.random_week_days,
        "last_complete_time": group.last_complete_time.isoformat(sep=" ", timespec="seconds"),
        "task_progress_time": group.task_progress_time.isoformat(sep=" ", timespec="seconds"),
        "next_run": next_run.isoformat(sep=" ", timespec="seconds") if next_run else None,
        "due": bool(next_run and next_run <= now),
        "schedule_status": "pending" if next_run and next_run <= now else "waiting",
        "priority": scheduler.priority,
        "task_progress": {
            "completed_task_list": str(group.completed_task_list),
            "failed_task_list": str(group.failed_task_list),
            "unfinished_task_list": str(group.unfinished_task_list),
        },
        "tasks": [{
            "task_name": task.task_name,
            "task_display_name": display_name(task.task_name),
            "enable": task.enable,
            "has_private_config": bool(task.private_config),
            "status": ("failed" if task.task_name in failed else
                       "unfinished" if task.task_name in unfinished else
                       "completed" if task.task_name in completed else "pending"),
        } for task in group.task_list if task.task_name],
    }
    if include_item_type:
        # Preserve the original orchestration response key order and metadata.
        data = {"batch_id": data.pop("batch_id"), "item_type": group.item_type, **data}
    return data
