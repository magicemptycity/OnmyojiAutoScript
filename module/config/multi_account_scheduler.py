"""Independent per-account task Scheduler helpers.

Timed and orchestration tasks share the private scheduler switch, but the
base Scheduler policy (inherit public vs. reset to defaults) stays explicit.
"""
import random
from datetime import datetime, time, timedelta
from typing import Any

from module.config.model_overrides import model_with_field_overrides
from module.config.utils import convert_to_underscore, parse_next_server_schedule


def private_scheduler_enabled(private_config: dict, public_scheduler: Any) -> bool:
    override = private_config.get("scheduler", {})
    if isinstance(override, dict) and "enable" in override:
        return bool(override["enable"])
    return bool(getattr(public_scheduler, "enable", False))


def set_private_scheduler_enabled(private_config: dict, enabled: bool) -> None:
    private_config.setdefault("scheduler", {})["enable"] = enabled


def resolve_private_scheduler(public_scheduler: Any, private_config: dict, *,
                              inherit_public: bool = True, validate_raw_merge: bool = False) -> Any:
    if public_scheduler is None:
        return None
    override = private_config.get("scheduler", {})
    base = public_scheduler if inherit_public else public_scheduler.__class__()
    if validate_raw_merge:
        # Orchestration historically validates a JSON merge, including legacy extra fields.
        data = base.model_dump(mode="json")
        if isinstance(override, dict):
            data.update(override)
        return base.__class__.model_validate(data)
    return model_with_field_overrides(base, override if isinstance(override, dict) else {})


def has_private_task_overrides(private_config: dict) -> bool:
    """Keep timed runner policy: routing-only overrides do not reset public defaults."""
    for name, values in private_config.items():
        if not isinstance(values, dict):
            continue
        if convert_to_underscore(name) == "scheduler" and set(values) <= {"enable", "next_run"}:
            continue
        return True
    return False


def resolve_timed_scheduler(public_scheduler: Any, private_config: dict) -> Any:
    return resolve_private_scheduler(
        public_scheduler, private_config,
        inherit_public=not has_private_task_overrides(private_config), validate_raw_merge=True,
    )


def resolve_orchestration_scheduler(public_scheduler: Any, private_config: dict) -> Any:
    return resolve_private_scheduler(public_scheduler, private_config, validate_raw_merge=True)


def resolve_independent_scheduler(public_scheduler: Any, entry: Any) -> Any:
    """Scheduler is always private; config_mode only selects task parameters."""
    return resolve_private_scheduler(
        public_scheduler, entry.private_config,
        inherit_public=False,
        validate_raw_merge=True,
    )


def timed_next_run(entry: Any) -> Any:
    """The private Scheduler owns NextRun; the legacy task field is only a mirror."""
    from datetime import datetime
    raw = entry.private_config.get("scheduler", {}).get("next_run")
    if raw is None:
        return entry.next_run  # Transient objects created before the migration validator.
    return raw if isinstance(raw, datetime) else datetime.fromisoformat(str(raw).replace('Z', '+00:00'))


def set_timed_next_run(entry: Any, value: Any) -> None:
    entry.private_config.setdefault("scheduler", {})["next_run"] = value.strftime("%Y-%m-%d %H:%M:%S")
    entry.next_run = value


def quick_schedule_next_run(scheduler: Any, *, run_now: bool) -> datetime:
    """OAS quick-run/quick-wait policy shared by per-account schedulers."""
    now = datetime.now().replace(microsecond=0)
    if run_now:
        return now - timedelta(days=1)
    interval = getattr(scheduler, "success_interval", timedelta(days=1))
    float_time = getattr(scheduler, "float_time", time.min)
    float_seconds = float_time.hour * 3600 + float_time.minute * 60 + float_time.second
    random_float = random.randint(0, float_seconds)
    if getattr(scheduler, "server_update", time(hour=9)) == time(hour=9):
        return now + interval + timedelta(seconds=random_float)
    return parse_next_server_schedule(scheduler, random_float)
