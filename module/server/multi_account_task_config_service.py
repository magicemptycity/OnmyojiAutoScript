import copy
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel

from module.config.utils import convert_to_underscore
from module.server.main_manager import mm
from tasks.Component.config_base import TimeDelta


def has_task_script(task_name: str) -> bool:
    task_key = convert_to_underscore(task_name)
    tasks_root = Path.cwd() / "tasks"
    return any(
        directory.is_dir()
        and convert_to_underscore(directory.name) == task_key
        and (directory / "script_task.py").is_file()
        for directory in tasks_root.iterdir()
    )


def apply_private_args(task_args: dict, private: dict) -> dict:
    for group_name, arguments in private.items():
        if not isinstance(arguments, dict) or group_name not in task_args:
            continue
        for argument in task_args[group_name]:
            if argument.get("name") in arguments:
                argument["value"] = arguments[argument["name"]]
    return task_args


def public_account(library, identifier: str):
    account = library.find(identifier)
    if account is None:
        raise HTTPException(status_code=404, detail="公共账号不存在")
    return account


def task_account(section, account_index: int):
    accounts = [
        item for item in section.account_list
        if item.public_account_identifier.strip()
    ]
    if account_index < 1 or account_index > len(accounts):
        raise HTTPException(status_code=404, detail="运行账号不存在")
    return accounts[account_index - 1]


def serialize_group(group: BaseModel) -> list[dict]:
    schema = group.__class__.model_json_schema()
    values = group.model_dump()
    definitions = schema.get("$defs", {})
    result: list[dict] = []
    for name, definition in schema.get("properties", {}).items():
        if "default" not in definition:
            continue
        item = {
            "name": name,
            "title": definition.get("title", name),
            "description": definition.get("description", ""),
            "default": definition["default"],
            "value": values.get(name, definition["default"]),
            "type": definition.get("type", "enum"),
        }
        ref = definition.get("$ref")
        if ref:
            enum_name = ref.rsplit("/", 1)[-1]
            enum_values = definitions.get(enum_name, {}).get("enum")
            if enum_values:
                item["enumEnum"] = enum_values
        result.append(item)
    return result


def find_group(model: BaseModel, group_name: str):
    normalized = convert_to_underscore(group_name)
    group = getattr(model, normalized, None)
    if group is not None:
        return group
    matches = re.findall(r"\d+", normalized)
    index = int(matches[-1]) - 1 if matches else -1
    if index < 0:
        return None
    for field_name, value in model.__dict__.items():
        if field_name in normalized and isinstance(value, list) and index < len(value):
            return value[index]
    return None


def convert_argument(types: str, value):
    if types == "integer":
        return int(value)
    if types == "number":
        return float(value)
    if types == "boolean":
        return value.lower() in {"true", "1"} if isinstance(value, str) else bool(value)
    if types == "date_time":
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    if types == "time":
        return datetime.strptime(value, "%H:%M:%S").time()
    if types == "time_delta":
        day, clock = value.strip().split(maxsplit=1)
        parsed = datetime.strptime(clock, "%H:%M:%S")
        return TimeDelta(
            days=int(day), hours=parsed.hour, minutes=parsed.minute, seconds=parsed.second
        )
    if types == "weekday_multi":
        days = sorted({int(item.strip()) for item in str(value).split(",") if item.strip()})
        if any(day < 1 or day > 7 for day in days):
            raise ValueError("weekday must be between 1 and 7")
        return days
    return value


def default_private_config(
    script_name: str,
    task_name: str,
    *,
    include_scheduler: bool = False,
) -> dict:
    model = mm.config_cache(script_name).model
    task_config = getattr(model, convert_to_underscore(task_name), None)
    if not isinstance(task_config, BaseModel):
        raise HTTPException(status_code=400, detail="任务配置不存在")
    default_config = task_config.__class__()
    private: dict = {}
    for group_name, arguments in model.script_task(task_name).items():
        normalized_group = convert_to_underscore(group_name)
        if (not include_scheduler and normalized_group == "scheduler") or not isinstance(arguments, list):
            continue
        default_group = find_group(default_config, group_name)
        if not isinstance(default_group, BaseModel):
            continue
        values = {
            convert_to_underscore(item["name"]): item["value"]
            for item in serialize_group(default_group)
            if item.get("name")
        }
        if values:
            private[normalized_group] = values
    return private


def default_task_args(
    script_name: str,
    task_name: str,
    *,
    remove_scheduler: bool = False,
) -> dict:
    model = mm.config_cache(script_name).model
    task_key = convert_to_underscore(task_name)
    task_config = getattr(model, task_key, None)
    if not isinstance(task_config, BaseModel):
        raise HTTPException(status_code=400, detail="任务配置不存在")
    default_model = model.model_copy(deep=True)
    BaseModel.__setattr__(default_model, task_key, task_config.__class__())
    task_args = copy.deepcopy(default_model.script_task(task_name))
    if remove_scheduler:
        task_args.pop("scheduler", None)
    return task_args


def sync_task_account(account: Any, source: Any) -> None:
    account.sync_public_account(source)
