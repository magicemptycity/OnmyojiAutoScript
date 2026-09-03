import copy
import re
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ValidationError

from module.config.model_overrides import model_with_group_overrides
from module.config.utils import convert_to_underscore
from module.server.api_logger import ApiLoggingRoute
from module.server.main_manager import mm
from tasks.MultiAccountTaskOrchestration.config import (
    MultiAccountRepeatNewAccount,
    MultiAccountRepeatNewTask,
)
from tasks.Component.MultiAccount.shared_public_accounts import SharedPublicAccount
from tasks.MultiAccountTaskOrchestration.task_name_resolver import TASK_NAME_ALIASES, TaskNameResolver


multi_account_repeat_new_normal_app = APIRouter(route_class=ApiLoggingRoute)


def _section(script_name: str):
    section = getattr(mm.config_cache(script_name).model, "multi_account_repeat_new_normal", None)
    if section is None:
        raise HTTPException(status_code=404, detail="当前配置没有多账号多任务新")
    return section


def _library(script_name: str):
    library = getattr(mm.config_cache(script_name).model, "multi_account_shared_accounts", None)
    if library is None:
        raise HTTPException(status_code=404, detail="当前配置没有公共账号库")
    return library


def _save(script_name: str, **fields) -> None:
    mm.config_cache(script_name).save_selected_fields(fields)



def _has_task_script(task_name: str) -> bool:
    """仅允许添加存在可执行 script_task.py 的普通任务。"""
    task_key = convert_to_underscore(task_name)
    tasks_root = Path.cwd() / "tasks"
    return any(
        directory.is_dir()
        and convert_to_underscore(directory.name) == task_key
        and (directory / "script_task.py").is_file()
        for directory in tasks_root.iterdir()
    )

def _apply_private_args(task_args: dict, private: dict) -> dict:
    """将私有覆盖值套到 OASX 参数表单数据中。"""
    for group_name, arguments in private.items():
        if not isinstance(arguments, dict) or group_name not in task_args:
            continue
        for argument in task_args[group_name]:
            if argument.get("name") in arguments:
                argument["value"] = arguments[argument["name"]]
    return task_args


def _task_display_name(task_name: str) -> str:
    """返回任务在多账号页面使用的中文显示名称，内部任务标识仍保持不变。"""
    canonical_name = TaskNameResolver.resolve(task_name) or task_name.strip()
    aliases = TASK_NAME_ALIASES.get(canonical_name, [])
    return aliases[0] if aliases else canonical_name


def _public_account(library, identifier: str) -> SharedPublicAccount:
    account = library.find(identifier)
    if account is None:
        raise HTTPException(status_code=404, detail="公共账号不存在")
    return account


def _task_account(section, account_index: int) -> MultiAccountRepeatNewAccount:
    accounts = [item for item in section.account_list if item.public_account_identifier.strip()]
    if account_index < 1 or account_index > len(accounts):
        raise HTTPException(status_code=404, detail="运行账号不存在")
    return accounts[account_index - 1]


def _find_task_entry(account: MultiAccountRepeatNewAccount, task_name: str) -> MultiAccountRepeatNewTask | None:
    key = convert_to_underscore(task_name.strip())
    return next(
        (entry for entry in account.task_list if convert_to_underscore(entry.task_name) == key),
        None,
    )


def _task_entry(account: MultiAccountRepeatNewAccount, task_name: str) -> MultiAccountRepeatNewTask:
    entry = _find_task_entry(account, task_name)
    if entry is None:
        raise HTTPException(status_code=404, detail="账号没有配置该任务")
    return entry


def _ensure_disabled_task_entry(
    account: MultiAccountRepeatNewAccount,
    task_name: str,
) -> MultiAccountRepeatNewTask:
    """首次保存私有配置时创建任务项，但绝不因此启用任务。"""
    entry = _find_task_entry(account, task_name)
    if entry is None:
        entry = MultiAccountRepeatNewTask(
            task_name=convert_to_underscore(task_name.strip()),
            enable=False,
        )
        account.task_list.append(entry)
    return entry


def _serialize_group(group: BaseModel) -> list[dict]:
    """将单个公共配置组转换成 OASX 参数表单格式。"""
    schema = group.__class__.model_json_schema()
    values = group.model_dump()
    definitions = schema.get("$defs", {})
    result = []
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
            if "enum" in definitions.get(enum_name, {}):
                item["enumEnum"] = definitions[enum_name]["enum"]
        result.append(item)
    return result


def _find_group(model: BaseModel, group_name: str):
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


def _convert_argument(types: str, value):
    if types == "integer":
        return int(value)
    if types == "number":
        return float(value)
    if types == "boolean":
        return value.lower() in {"true", "1"} if isinstance(value, str) else bool(value)
    if types == "weekday_multi":
        days = sorted({int(item.strip()) for item in str(value).split(",") if item.strip()})
        if any(day < 1 or day > 7 for day in days):
            raise ValueError("weekday must be between 1 and 7")
        return days
    return value


def _default_private_config(script_name: str, task_name: str) -> dict:
    """Build a complete private override from the task model defaults only."""
    model = mm.config_cache(script_name).model
    task_config = getattr(model, convert_to_underscore(task_name), None)
    if not isinstance(task_config, BaseModel):
        raise HTTPException(status_code=400, detail="任务配置不存在")
    default_config = task_config.__class__()
    current_args = model.script_task(task_name)
    private: dict = {}
    for group_name, arguments in current_args.items():
        normalized_group = convert_to_underscore(group_name)
        if normalized_group == "scheduler" or not isinstance(arguments, list):
            continue
        default_group = _find_group(default_config, group_name)
        if not isinstance(default_group, BaseModel):
            continue
        values = {
            convert_to_underscore(item["name"]): item["value"]
            for item in _serialize_group(default_group)
            if item.get("name")
        }
        if values:
            private[normalized_group] = values
    return private


def _default_task_args(script_name: str, task_name: str, *, remove_scheduler: bool = False) -> dict:
    """使用任务模型默认值生成设置页参数，避免继承已修改的公共配置。"""
    model = mm.config_cache(script_name).model
    task_key = convert_to_underscore(task_name)
    task_config = getattr(model, task_key, None)
    if not isinstance(task_config, BaseModel):
        raise HTTPException(status_code=400, detail="任务配置不存在")
    default_model = model.model_copy(deep=True)
    setattr(default_model, task_key, task_config.__class__())
    task_args = copy.deepcopy(default_model.script_task(task_name))
    if remove_scheduler:
        task_args.pop("scheduler", None)
    return task_args


def _sync_task_account(account: MultiAccountRepeatNewAccount, source: SharedPublicAccount) -> None:
    account.sync_public_account(source)


def _parse_account_progress_task_names(
    account: MultiAccountRepeatNewAccount,
    raw_names: str,
    label: str,
) -> list[str]:
    """Parse Chinese aliases/internal names and only accept enabled tasks of this account."""
    enabled_names = {
        convert_to_underscore(task.task_name)
        for task in account.task_list
        if task.task_name and task.enable
    }
    names: list[str] = []
    invalid: list[str] = []
    for raw_name in re.split(r"[,\n]", raw_names or ""):
        value = raw_name.strip()
        if not value:
            continue
        task_key = convert_to_underscore(TaskNameResolver.resolve(value) or value)
        if task_key not in enabled_names:
            invalid.append(value)
            continue
        if task_key not in names:
            names.append(task_key)
    if invalid:
        raise HTTPException(
            status_code=400,
            detail=f"{label}包含当前账号未启用或不存在的任务：{', '.join(invalid)}",
        )
    return names


def _save_account_task_progress(
    account: MultiAccountRepeatNewAccount,
    completed: list[str],
    failed: list[str],
    unfinished: list[str],
) -> None:
    """Persist the normal-mode recovery lists using the same display names as its runner."""
    account.completed_task_list = "\n".join(
        _task_display_name(name) for name in dict.fromkeys(completed)
    )
    account.failed_task_list = "\n".join(
        _task_display_name(name) for name in dict.fromkeys(failed)
    )
    account.unfinished_task_list = "\n".join(
        _task_display_name(name) for name in dict.fromkeys(unfinished)
    )
    account.task_progress_time = datetime.now()


@multi_account_repeat_new_normal_app.get('/{script_name}/multi_account_repeat_new_normal/accounts')
async def list_task_accounts(script_name: str):
    section = _section(script_name)
    accounts = []
    for index, item in enumerate([entry for entry in section.account_list if entry.public_account_identifier.strip()], start=1):
        completed = set(item.completed_task_names)
        failed = set(item.failed_task_names)
        unfinished = set(item.unfinished_task_names)
        # 旧的完成清单可能来自昨天；只有当天记录过进度才用于页面状态。
        progress_time = max(item.task_progress_time, item.last_complete_time)
        progress_is_today = progress_time.date() == datetime.now().date()
        accounts.append({
            "index": index,
            "public_account_identifier": item.public_account_identifier,
            "character": item.character,
            "svr": item.svr,
            "account": item.account,
            "account_alias": item.account_alias,
            "apple_or_android": item.apple_or_android,
            "last_complete_time": item.last_complete_time.isoformat(sep=" ", timespec="seconds"),
            "task_progress_time": item.task_progress_time.isoformat(sep=" ", timespec="seconds"),
            "task_progress": {
                "completed_task_list": str(item.completed_task_list),
                "failed_task_list": str(item.failed_task_list),
                "unfinished_task_list": str(item.unfinished_task_list),
            },
            "tasks": [
                {
                    "task_name": task.task_name,
                    "task_display_name": _task_display_name(task.task_name),
                    "enabled": task.enable,
                    "has_private_config": bool(task.private_config),
                    "status": (
                        "failed" if progress_is_today and task.task_name in failed
                        else "unfinished" if progress_is_today and task.task_name in unfinished
                        else "completed" if progress_is_today and task.task_name in completed
                        else "pending"
                    ),
                }
                for task in item.task_list if task.task_name
            ],
        })
    return {"accounts": accounts}


@multi_account_repeat_new_normal_app.post('/{script_name}/multi_account_repeat_new_normal/accounts')
async def add_task_account(script_name: str, public_account_identifier: str):
    section = _section(script_name)
    source = _public_account(_library(script_name), public_account_identifier)
    if any(item.public_account_identifier == source.identifier for item in section.account_list):
        return True
    account = MultiAccountRepeatNewAccount()
    _sync_task_account(account, source)
    section.account_list = [item for item in section.account_list if item.public_account_identifier.strip()]
    section.account_list.append(account)
    _save(script_name, multi_account_repeat_new_normal=section)
    return True


@multi_account_repeat_new_normal_app.delete('/{script_name}/multi_account_repeat_new_normal/accounts/{account_index}')
async def delete_task_account(script_name: str, account_index: int):
    section = _section(script_name)
    account = _task_account(section, account_index)
    section.account_list.remove(account)
    _save(script_name, multi_account_repeat_new_normal=section)
    return True


@multi_account_repeat_new_normal_app.post('/{script_name}/multi_account_repeat_new_normal/accounts/{account_index}/tasks')
async def add_task(script_name: str, account_index: int, task_name: str):
    section = _section(script_name)
    account = _task_account(section, account_index)
    key = convert_to_underscore(task_name.strip())
    if not key or getattr(mm.config_cache(script_name).model, key, None) is None:
        raise HTTPException(status_code=400, detail="任务不存在")
    if key.startswith("multi_account_repeat"):
        raise HTTPException(status_code=400, detail="不能嵌套多账号多任务")
    if not _has_task_script(key):
        raise HTTPException(status_code=400, detail="该功能没有可执行任务，不能添加到多账号任务")
    existing = next((item for item in account.task_list if convert_to_underscore(item.task_name) == key), None)
    if existing is not None:
        existing.enable = True
        _save(script_name, multi_account_repeat_new_normal=section)
        return True
    account.task_list.append(MultiAccountRepeatNewTask(task_name=key, enable=True))
    _save(script_name, multi_account_repeat_new_normal=section)
    return True


@multi_account_repeat_new_normal_app.put('/{script_name}/multi_account_repeat_new_normal/accounts/{account_index}/tasks/order')
async def reorder_tasks(script_name: str, account_index: int, task_names: str):
    """保存已启用任务的执行顺序，不触碰停用任务及其私有配置。"""
    section = _section(script_name)
    account = _task_account(section, account_index)
    enabled = [item for item in account.task_list if item.enable]
    requested_names = [
        convert_to_underscore(name.strip())
        for name in re.split(r'[,\n]', task_names)
        if name.strip()
    ]
    enabled_names = [convert_to_underscore(item.task_name) for item in enabled]
    if (
        len(requested_names) != len(enabled_names)
        or len(set(requested_names)) != len(requested_names)
        or set(requested_names) != set(enabled_names)
    ):
        raise HTTPException(status_code=400, detail="排序任务必须与当前已启用任务完全一致")

    enabled_by_name = {convert_to_underscore(item.task_name): item for item in enabled}
    account.task_list = [
        *(enabled_by_name[name] for name in requested_names),
        *(item for item in account.task_list if not item.enable),
    ]
    _save(script_name, multi_account_repeat_new_normal=section)
    return True


@multi_account_repeat_new_normal_app.delete('/{script_name}/multi_account_repeat_new_normal/accounts/{account_index}/tasks/{task_name}')
async def delete_task(script_name: str, account_index: int, task_name: str):
    section = _section(script_name)
    account = _task_account(section, account_index)
    entry = _task_entry(account, task_name)
    entry.enable = False
    _save(script_name, multi_account_repeat_new_normal=section)
    return True


@multi_account_repeat_new_normal_app.put('/{script_name}/multi_account_repeat_new_normal/accounts/{account_index}/tasks/{task_name}/enable')
async def set_task_enable(script_name: str, account_index: int, task_name: str, value: str):
    section = _section(script_name)
    _task_entry(_task_account(section, account_index), task_name).enable = _convert_argument("boolean", value)
    _save(script_name, multi_account_repeat_new_normal=section)
    return True


@multi_account_repeat_new_normal_app.put('/{script_name}/multi_account_repeat_new_normal/accounts/{account_index}/tasks/{task_name}/status')
async def set_task_status(script_name: str, account_index: int, task_name: str, value: str):
    """Manually correct one normal-mode task's recovery status without touching its private config."""
    section = _section(script_name)
    account = _task_account(section, account_index)
    entry = _task_entry(account, task_name)
    status = value.strip().lower()
    if status not in {"completed", "failed", "unfinished", "pending"}:
        raise HTTPException(status_code=400, detail="任务状态只能是 completed、failed、unfinished 或 pending")

    task_key = convert_to_underscore(entry.task_name)
    completed = [name for name in account.completed_task_names if name != task_key]
    failed = [name for name in account.failed_task_names if name != task_key]
    unfinished = [name for name in account.unfinished_task_names if name != task_key]
    if status == "completed":
        completed.append(task_key)
    elif status == "failed":
        failed.append(task_key)
    elif status == "unfinished":
        unfinished.append(task_key)
    elif account.last_complete_time.date() == datetime.now().date():
        # "未开始" must not leave the account silently skipped by today's completion marker.
        account.last_complete_time = datetime(2023, 1, 1)

    _save_account_task_progress(account, completed, failed, unfinished)
    _save(script_name, multi_account_repeat_new_normal=section)
    return True


@multi_account_repeat_new_normal_app.put('/{script_name}/multi_account_repeat_new_normal/accounts/{account_index}/task-progress')
async def set_account_task_progress(
    script_name: str,
    account_index: int,
    completed_task_list: str = "",
    failed_task_list: str = "",
    unfinished_task_list: str = "",
):
    """Bulk-edit normal-mode recovery lists with alias validation and exclusive task states."""
    section = _section(script_name)
    account = _task_account(section, account_index)
    completed = _parse_account_progress_task_names(account, completed_task_list, "已完成任务列表")
    failed = _parse_account_progress_task_names(account, failed_task_list, "失败任务列表")
    unfinished = _parse_account_progress_task_names(account, unfinished_task_list, "未完成任务列表")

    # A task has exactly one state. Recovery states win over completed state.
    failed_set = set(failed)
    unfinished = [name for name in unfinished if name not in failed_set]
    recovery_set = failed_set | set(unfinished)
    completed = [name for name in completed if name not in recovery_set]
    _save_account_task_progress(account, completed, failed, unfinished)
    _save(script_name, multi_account_repeat_new_normal=section)
    return True


@multi_account_repeat_new_normal_app.put('/{script_name}/multi_account_repeat_new_normal/accounts/{account_index}/last-complete-time')
async def set_account_last_complete_time(script_name: str, account_index: int, value: str):
    section = _section(script_name)
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.replace(tzinfo=None)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="完成时间格式错误，应为 YYYY-MM-DD HH:MM:SS") from exc
    _task_account(section, account_index).last_complete_time = parsed
    _save(script_name, multi_account_repeat_new_normal=section)
    return True


@multi_account_repeat_new_normal_app.post('/{script_name}/multi_account_repeat_new_normal/accounts/{account_index}/rerun')
async def rerun_account_tasks(script_name: str, account_index: int):
    """Make one account eligible for a complete normal-mode run while retaining all task/private settings."""
    section = _section(script_name)
    account = _task_account(section, account_index)
    account.last_complete_time = datetime(2023, 1, 1)
    _save_account_task_progress(account, [], [], [])
    _save(script_name, multi_account_repeat_new_normal=section)
    return True


@multi_account_repeat_new_normal_app.get('/{script_name}/multi_account_repeat_new_normal/public-args')
async def get_public_args(script_name: str):
    section = _section(script_name)
    return {
        "scheduler": _serialize_group(section.scheduler),
        "multi_account_repeat_new_config": _serialize_group(section.multi_account_repeat_new_config),
    }


@multi_account_repeat_new_normal_app.put('/{script_name}/multi_account_repeat_new_normal/public-args/{group}/{argument}/value')
async def set_public_arg(script_name: str, group: str, argument: str, types: str, value):
    section = _section(script_name)
    group_name = convert_to_underscore(group)
    if group_name not in {"scheduler", "multi_account_repeat_new_config"}:
        raise HTTPException(status_code=400, detail="不支持修改该公共配置")
    candidate = copy.deepcopy(section)
    try:
        candidate = model_with_group_overrides(candidate, {
            group_name: {argument: _convert_argument(types, value)},
        })
    except (ValidationError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=f"公共参数无效：{exc}") from exc
    _save(script_name, multi_account_repeat_new_normal=candidate)
    return True


@multi_account_repeat_new_normal_app.get('/{script_name}/multi_account_repeat_new_normal/accounts/{account_index}/tasks/{task_name}/args')
async def get_private_args(script_name: str, account_index: int, task_name: str):
    account = _task_account(_section(script_name), account_index)
    entry = _find_task_entry(account, task_name)
    task_args = _default_task_args(
        script_name,
        entry.task_name if entry is not None else task_name,
        remove_scheduler=True,
    )
    return _apply_private_args(task_args, entry.private_config) if entry is not None else task_args


@multi_account_repeat_new_normal_app.put('/{script_name}/multi_account_repeat_new_normal/accounts/{account_index}/tasks/{task_name}/{group}/{argument}/value')
async def set_private_arg(script_name: str, account_index: int, task_name: str, group: str, argument: str, types: str, value):
    section = _section(script_name)
    account = _task_account(section, account_index)
    if convert_to_underscore(group) == "scheduler":
        raise HTTPException(status_code=400, detail="不能在私有配置中修改调度参数")
    task_config = getattr(mm.config_cache(script_name).model, convert_to_underscore(task_name), None)
    if not isinstance(task_config, BaseModel):
        raise HTTPException(status_code=400, detail="任务配置不存在")
    entry = _ensure_disabled_task_entry(account, task_name)
    private = copy.deepcopy(entry.private_config)
    private.setdefault(convert_to_underscore(group), {})[convert_to_underscore(argument)] = _convert_argument(types, value)
    candidate = task_config.__class__()
    try:
        candidate = model_with_group_overrides(candidate, private)
    except (ValidationError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=f"私有参数无效：{exc}") from exc
    entry.private_config = private
    _save(script_name, multi_account_repeat_new_normal=section)
    return True


@multi_account_repeat_new_normal_app.post('/{script_name}/multi_account_repeat_new_normal/accounts/{account_index}/tasks/{task_name}/private/copy')
async def copy_private_args_to_accounts(
        script_name: str,
        account_index: int,
        task_name: str,
        target_account_indexes: str,
):
    """复制当前账号该任务的私有覆盖配置，不复制任务状态与调度信息。"""
    section = _section(script_name)
    source_account = _task_account(section, account_index)
    source_entry = _task_entry(source_account, task_name)
    try:
        target_indexes = {
            int(item.strip())
            for item in target_account_indexes.split(',')
            if item.strip()
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="目标账号序号格式错误") from exc
    if not target_indexes:
        raise HTTPException(status_code=400, detail="请至少选择一个目标账号")

    copied_count = 0
    for target_index in target_indexes:
        if target_index == account_index:
            continue
        target_account = _task_account(section, target_index)
        target_entry = next(
            (
                entry
                for entry in target_account.task_list
                if convert_to_underscore(entry.task_name)
                == convert_to_underscore(source_entry.task_name)
            ),
            None,
        )
        if target_entry is None:
            # 目标账号未配置此任务时，先创建任务项，默认状态不继承源账号。
            target_entry = MultiAccountRepeatNewTask(task_name=source_entry.task_name)
            target_account.task_list.append(target_entry)
        target_entry.private_config = copy.deepcopy(source_entry.private_config)
        copied_count += 1
    if not copied_count:
        raise HTTPException(status_code=400, detail="没有可复制的目标账号")
    _save(script_name, multi_account_repeat_new_normal=section)
    return True


@multi_account_repeat_new_normal_app.put('/{script_name}/multi_account_repeat_new_normal/accounts/{account_index}/tasks/{task_name}/private/default')
async def reset_private_args_to_default(script_name: str, account_index: int, task_name: str):
    section = _section(script_name)
    entry = _find_task_entry(_task_account(section, account_index), task_name)
    # 未启用且未保存过私有配置时，本来就在使用默认值，无需制造任务项。
    if entry is None:
        return True
    private = _default_private_config(script_name, entry.task_name)
    task_config = getattr(mm.config_cache(script_name).model, convert_to_underscore(entry.task_name), None)
    candidate = task_config.__class__()
    try:
        candidate = model_with_group_overrides(candidate, private)
    except (ValidationError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=f"默认私有参数无效：{exc}") from exc
    entry.private_config = private
    _save(script_name, multi_account_repeat_new_normal=section)
    return True


