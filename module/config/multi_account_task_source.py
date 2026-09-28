"""Shared task-configuration source selection for independent multi-account tasks.

Task-local scheduling policy and persistence remain mode-specific. The runtime
record is applied before user overrides so an older run cannot revert settings.
"""
import copy
from typing import Any

from pydantic import BaseModel

from module.config.model_overrides import model_with_group_overrides


def uses_private_config(config_mode: Any) -> bool:
    return getattr(config_mode, "value", config_mode) == "private"


def _merge_runtime(base: dict, recorded: dict) -> dict:
    result = copy.deepcopy(base)
    for key, value in recorded.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge_runtime(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def effective_task_config(public_config: BaseModel, entry: Any, *, private_scheduler: bool = False) -> BaseModel:
    """Build a validated copy without mutating the public task configuration."""
    private = uses_private_config(entry.config_mode)
    active = public_config.__class__() if private else copy.deepcopy(public_config)
    if entry.runtime_record:
        active = active.__class__.model_validate(
            _merge_runtime(active.model_dump(), entry.runtime_record)
        )
    overrides = entry.private_config if private else {
        "scheduler": entry.private_config.get("scheduler", {})
    }
    active = model_with_group_overrides(active, overrides)
    if private_scheduler:
        from module.config.multi_account_scheduler import resolve_independent_scheduler
        scheduler = resolve_independent_scheduler(getattr(public_config, "scheduler", None), entry)
        if scheduler is not None:
            BaseModel.__setattr__(active, "scheduler", scheduler)
    return active
