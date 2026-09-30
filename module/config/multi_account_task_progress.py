"""Shared runner task-local progress transitions for timed and orchestration singles.

Account/group completion lists and scheduler next-run remain mode-owned.
"""
from datetime import datetime
from typing import Any

from module.config.multi_account_scheduler import quick_schedule_next_run

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


def settle_completed_group(owner: Any, *, now: datetime | None = None) -> bool:
    """Finish a group whose enabled tasks were all completed today, without rerunning them.

    Also repairs an already-completed group left with a past-due next_run.
    Never advances incomplete, failed, unfinished, or yesterday's progress.
    """
    now = now or datetime.now()
    names = set(owner.task_names)
    if (not names or owner.task_progress_time.date() != now.date()
            or owner.failed_task_names or owner.unfinished_task_names
            or not names.issubset(set(owner.completed_task_names))):
        return False
    if owner.last_complete_time.date() == now.date() and owner.scheduler.next_run > now:
        return False
    owner.last_complete_time = now
    owner.scheduler.next_run = quick_schedule_next_run(owner.scheduler, run_now=False)
    return True


def normal_completed_today(account: Any, *, now: datetime | None = None) -> set[str]:
    """Only today's account-level completion list can suppress a normal task."""
    now = now or datetime.now()
    return set(account.completed_task_names) if account.task_progress_time.date() == now.date() else set()


def reconcile_normal_account_completion(
    account: Any, *, now: datetime | None = None, invalidate_on_incomplete: bool = True,
) -> bool:
    """Keep the account's daily completion marker aligned with its enabled tasks.

    Return whether the marker changed; never alter scheduling or task runtime records.
    """
    now = now or datetime.now()
    names = set(account.task_names)
    complete = (bool(names) and account.task_progress_time.date() == now.date()
                and not account.failed_task_names and not account.unfinished_task_names
                and names.issubset(normal_completed_today(account, now=now)))
    if complete and account.last_complete_time.date() != now.date():
        account.last_complete_time = now
        return True
    if invalidate_on_incomplete and not complete and account.last_complete_time.date() == now.date():
        account.last_complete_time = datetime(2023, 1, 1)
        return True
    return False
