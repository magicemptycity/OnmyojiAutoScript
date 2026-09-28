"""Account-level schedule metrics for fixed groups and mixed orchestration."""
from datetime import datetime


def schedule_item_metrics(groups: list[dict], singles: list[dict] = (), *, runnable: bool,
                          now: datetime | None = None) -> dict:
    now = now or datetime.now()
    # An empty group cannot execute even if its scheduler is enabled.
    items = [group for group in groups if group["enable"] and group["enabled_task_count"] > 0]
    items.extend(single for single in singles if single["enable"])
    next_runs = [datetime.fromisoformat(item["next_run"]) for item in items if item.get("next_run")]
    failed = sum(
        item.get("failed_task_count", 0) > 0 if "batch_id" in item
        else item.get("task_progress", {}).get("status") in {"failed", "unfinished"}
        for item in items
    )
    return {
        "next_run": min(next_runs).isoformat(sep=" ", timespec="seconds") if next_runs else None,
        "enabled_schedule_count": len(items),
        "due_schedule_count": sum(run <= now for run in next_runs) if runnable else 0,
        "failed_schedule_count": failed,
    }
