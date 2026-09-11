"""Safe JSON-style overrides for nested Pydantic task configuration models."""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel

from module.config.utils import convert_to_underscore


def model_with_field_overrides(
    model: BaseModel,
    overrides: Mapping[str, Any],
    *,
    ignore_unknown: bool = False,
) -> BaseModel:
    """Returns a validated copy after applying raw JSON field values.

    Private configuration is persisted as JSON, where values such as timedelta,
    datetime, time and enum instances are strings. Set ``ignore_unknown`` for
    legacy persisted fields that no longer exist in the current task model.
    Do not assign those raw
    values to an already typed model and then call ``model_dump``: custom
    serializers may receive the wrong Python type before Pydantic can validate
    it.  Merge into serialized data first, then validate the whole model.
    """
    data = model.model_dump(mode="json")
    for field_name, value in overrides.items():
        key = convert_to_underscore(str(field_name))
        if key not in data:
            if ignore_unknown:
                continue
            raise ValueError(f"找不到参数：{field_name}")
        data[key] = copy.deepcopy(value)
    return model.__class__.model_validate(data)


def _find_group_data(
    model: BaseModel,
    data: dict[str, Any],
    group_name: str,
) -> dict[str, Any] | None:
    """Finds a serialized nested group, including indexed config group lists."""
    normalized = convert_to_underscore(group_name)
    direct = data.get(normalized)
    if isinstance(direct, dict):
        return direct

    matches = re.findall(r"\d+", normalized)
    index = int(matches[-1]) - 1 if matches else -1
    if index < 0:
        return None
    for field_name, value in model.__dict__.items():
        if field_name not in normalized or not isinstance(value, list):
            continue
        serialized_list = data.get(field_name)
        if not isinstance(serialized_list, list) or index >= len(serialized_list):
            continue
        group = serialized_list[index]
        if isinstance(group, dict):
            return group
    return None


def model_with_group_overrides(
    model: BaseModel,
    overrides: Mapping[str, Any],
) -> BaseModel:
    """Returns a validated copy after applying private config group overrides."""
    data = model.model_dump(mode="json")
    for group_name, arguments in overrides.items():
        if not isinstance(arguments, Mapping):
            continue
        group_data = _find_group_data(model, data, str(group_name))
        if group_data is None:
            raise ValueError(f"找不到任务参数分组：{group_name}")
        for argument_name, value in arguments.items():
            key = convert_to_underscore(str(argument_name))
            if key not in group_data:
                raise ValueError(f"参数分组 {group_name} 中不存在参数：{argument_name}")
            group_data[key] = copy.deepcopy(value)
    return model.__class__.model_validate(data)
