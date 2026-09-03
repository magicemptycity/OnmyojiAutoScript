import copy
import random
import re
from datetime import datetime, time, timedelta
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ValidationError

from module.config.model_overrides import model_with_field_overrides, model_with_group_overrides
from module.config.utils import (
    convert_to_underscore,
    parse_next_server_weekday,
    parse_tomorrow_server,
)
from module.server.api_logger import ApiLoggingRoute
from module.server.main_manager import mm
from tasks.MultiAccountRepeatTimed.config import (
    MultiAccountRepeatTimedAccount,
    MultiAccountRepeatTimedTask,
)
from tasks.Component.MultiAccount.shared_public_accounts import SharedPublicAccount
from tasks.Component.config_base import TimeDelta
from tasks.MultiAccountRepeatTimed.task_name_resolver import TASK_NAME_ALIASES, TaskNameResolver


multi_account_repeat_timed_app = APIRouter(route_class=ApiLoggingRoute)


def _section(script_name: str):
    section = getattr(mm.config_cache(script_name).model, "multi_account_repeat_timed", None)
    if section is None:
        raise HTTPException(status_code=404, detail="当前配置没有多账号多任务定时")
    return section


def _library(script_name: str):
    library = getattr(mm.config_cache(script_name).model, "multi_account_shared_accounts", None)
    if library is None:
        raise HTTPException(status_code=404, detail="当前配置没有公共账号库")
    return library


def _save(script_name: str, **fields) -> None:
    mm.config_cache(script_name).save_selected_fields(fields)


async def _broadcast_multi_account_overview(script_name: str) -> None:
    """通过当前 OAS WebSocket 立即通知虚拟调度总览重排。"""
    process = mm.script_process.get(script_name)
    if process is not None:
        await process.broadcast_state({
            "multi_account_overview": {"kind": "timed"},
        })


def _entry_scheduler_enabled(script_name: str, entry: MultiAccountRepeatTimedTask) -> bool:
    """读取任务自身 scheduler.enable，账号列表只展示已启用任务。"""
    private_scheduler = entry.private_config.get("scheduler", {})
    if isinstance(private_scheduler, dict) and "enable" in private_scheduler:
        return bool(private_scheduler["enable"])
    task_config = getattr(mm.config_cache(script_name).model, convert_to_underscore(entry.task_name), None)
    scheduler = getattr(task_config, "scheduler", None)
    return bool(getattr(scheduler, "enable", False))


def _entry_scheduler(script_name: str, entry: MultiAccountRepeatTimedTask):
    """返回账号任务实际使用的 scheduler，保持与执行器的私有覆盖规则一致。"""
    task_config = getattr(mm.config_cache(script_name).model, convert_to_underscore(entry.task_name), None)
    public_scheduler = getattr(task_config, "scheduler", None)
    if public_scheduler is None:
        return None
    private_scheduler = entry.private_config.get("scheduler", {})
    private_keys = set(private_scheduler) if isinstance(private_scheduler, dict) else set()
    use_public = private_keys <= {"enable", "next_run"}
    scheduler = copy.deepcopy(public_scheduler) if use_public else public_scheduler.__class__()
    if isinstance(private_scheduler, dict):
        # 私有配置在 JSON 中存储为字符串；统一交给 Pydantic 恢复时间、间隔和枚举类型。
        scheduler = model_with_field_overrides(scheduler, private_scheduler)
    return scheduler


def _scheduler_next_run(scheduler, *, run_now: bool) -> datetime:
    """按 OAS quick run/quick wait 规则计算账号任务下次时间。"""
    if run_now:
        return datetime.now().replace(microsecond=0) - timedelta(days=1)
    start = datetime.now().replace(microsecond=0)
    interval = getattr(scheduler, "success_interval", timedelta(days=1))
    next_run = start + interval
    float_time = getattr(scheduler, "float_time", time.min)
    float_seconds = float_time.hour * 3600 + float_time.minute * 60 + float_time.second
    random_float = random.randint(0, float_seconds)
    server_update = getattr(scheduler, "server_update", time(hour=9))
    if server_update == time(hour=9):
        return next_run + timedelta(seconds=random_float)
    schedule_mode = getattr(scheduler, "schedule_mode", "interval_days")
    schedule_mode = getattr(schedule_mode, "value", schedule_mode)
    if schedule_mode == "weekday":
        return parse_next_server_weekday(
            server_update,
            getattr(scheduler, "weekdays", list(range(1, 8))),
            random_float,
        )
    return parse_tomorrow_server(
        server_update,
        getattr(scheduler, "delay_date", 1),
        random_float,
    )


def _refresh_outer_scheduler(script_name: str, section) -> None:
    """保存前刷新外层任务时间，使其始终指向账号任务的最早时间。"""
    next_runs = [
        entry.next_run
        for account in section.account_list
        for entry in account.task_list
        if _entry_scheduler_enabled(script_name, entry) and entry.task_name and entry.next_run
    ]
    if next_runs:
        section.scheduler.next_run = min(next_runs).replace(microsecond=0)


def _save_timed_section(script_name: str, section) -> None:
    """保存定时版配置，并同步外层多账号任务的 next_run。"""
    _refresh_outer_scheduler(script_name, section)
    _save(script_name, multi_account_repeat_timed=section)


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


def _task_account(section, account_index: int) -> MultiAccountRepeatTimedAccount:
    accounts = [item for item in section.account_list if item.public_account_identifier.strip()]
    if account_index < 1 or account_index > len(accounts):
        raise HTTPException(status_code=404, detail="运行账号不存在")
    return accounts[account_index - 1]


def _find_task_entry(account: MultiAccountRepeatTimedAccount, task_name: str) -> MultiAccountRepeatTimedTask | None:
    key = convert_to_underscore(task_name.strip())
    return next(
        (entry for entry in account.task_list if convert_to_underscore(entry.task_name) == key),
        None,
    )


def _task_entry(account: MultiAccountRepeatTimedAccount, task_name: str) -> MultiAccountRepeatTimedTask:
    entry = _find_task_entry(account, task_name)
    if entry is None:
        raise HTTPException(status_code=404, detail="账号没有配置该任务")
    return entry


def _ensure_disabled_task_entry(
    account: MultiAccountRepeatTimedAccount,
    task_name: str,
) -> MultiAccountRepeatTimedTask:
    """首次保存私有配置时创建任务项，并显式保持任务调度器停用。"""
    entry = _find_task_entry(account, task_name)
    if entry is None:
        entry = MultiAccountRepeatTimedTask(
            task_name=convert_to_underscore(task_name.strip()),
            private_config={"scheduler": {"enable": False}},
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
    """转换 OASX 表单值，确保私有 Pydantic 配置可被序列化和校验。"""
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
            days=int(day),
            hours=parsed.hour,
            minutes=parsed.minute,
            seconds=parsed.second,
        )
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
        if not isinstance(arguments, list):
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


def _timed_task_overview_sort_key(
    item: dict,
    index: int,
) -> tuple:
    """OAS 总览顺序：到点任务按优先级，等待任务按 NextRun 升序。"""
    next_run = item["next_run_value"]
    if item["schedule_status"] == "pending":
        return (0, item["priority"], next_run, index)
    return (1, next_run, index)


def _sync_task_account(account: MultiAccountRepeatTimedAccount, source: SharedPublicAccount) -> None:
    account.sync_public_account(source)


@multi_account_repeat_timed_app.get('/{script_name}/multi_account_repeat_timed/accounts')
async def list_task_accounts(script_name: str):
    section = _section(script_name)
    accounts = []
    for index, item in enumerate([entry for entry in section.account_list if entry.public_account_identifier.strip()], start=1):
        completed = set(item.completed_task_names)
        failed = set(item.failed_task_names)
        unfinished = set(item.unfinished_task_names)
        task_rows = []
        now = datetime.now()
        for task_index, task in enumerate(item.task_list):
            if not task.task_name:
                continue
            scheduler = _entry_scheduler(script_name, task)
            enabled = _entry_scheduler_enabled(script_name, task)
            next_run = task.next_run
            schedule_status = "pending" if enabled and next_run <= now else "waiting"
            try:
                priority = int(getattr(scheduler, "priority", 5))
            except (TypeError, ValueError):
                priority = 5
            task_rows.append({
                "task_name": task.task_name,
                "task_display_name": _task_display_name(task.task_name),
                "has_private_config": bool(task.private_config),
                "enabled": enabled,
                "next_run": next_run.isoformat(sep=" ", timespec="seconds"),
                "next_run_value": next_run,
                "priority": priority,
                "schedule_status": schedule_status,
                "status": (
                    "failed" if task.task_name in failed
                    else "unfinished" if task.task_name in unfinished
                    else "completed" if task.task_name in completed
                    else "pending"
                ),
                "_index": task_index,
            })
        task_rows.sort(
            key=lambda row: _timed_task_overview_sort_key(row, row["_index"])
        )
        for row in task_rows:
            row.pop("next_run_value", None)
            row.pop("priority", None)
            row.pop("_index", None)
        accounts.append({
            "index": index,
            "public_account_identifier": item.public_account_identifier,
            "character": item.character,
            "svr": item.svr,
            "account": item.account,
            "account_alias": item.account_alias,
            "apple_or_android": item.apple_or_android,
            "tasks": task_rows,
        })
    return {"accounts": accounts}


@multi_account_repeat_timed_app.post('/{script_name}/multi_account_repeat_timed/accounts')
async def add_task_account(script_name: str, public_account_identifier: str):
    section = _section(script_name)
    source = _public_account(_library(script_name), public_account_identifier)
    if any(item.public_account_identifier == source.identifier for item in section.account_list):
        return True
    account = MultiAccountRepeatTimedAccount()
    _sync_task_account(account, source)
    section.account_list = [item for item in section.account_list if item.public_account_identifier.strip()]
    section.account_list.append(account)
    _save_timed_section(script_name, section)
    return True


@multi_account_repeat_timed_app.delete('/{script_name}/multi_account_repeat_timed/accounts/{account_index}')
async def delete_task_account(script_name: str, account_index: int):
    section = _section(script_name)
    account = _task_account(section, account_index)
    section.account_list.remove(account)
    _save_timed_section(script_name, section)
    return True


@multi_account_repeat_timed_app.post('/{script_name}/multi_account_repeat_timed/accounts/{account_index}/tasks')
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
    existing = next(
        (item for item in account.task_list
         if convert_to_underscore(item.task_name) == key),
        None,
    )
    if existing is not None:
        # 重新添加等同于重新启用，原有私有配置和调度时间全部保留。
        existing.private_config.setdefault("scheduler", {})["enable"] = True
        _save_timed_section(script_name, section)
        return True
    account.task_list.append(
        MultiAccountRepeatTimedTask(
            task_name=key,
            private_config={"scheduler": {"enable": True}},
        )
    )
    _save_timed_section(script_name, section)
    return True


@multi_account_repeat_timed_app.delete('/{script_name}/multi_account_repeat_timed/accounts/{account_index}/tasks/{task_name}')
async def delete_task(script_name: str, account_index: int, task_name: str):
    section = _section(script_name)
    account = _task_account(section, account_index)
    entry = _task_entry(account, task_name)
    # DELETE 接口保留兼容旧客户端，但语义改为关闭 scheduler；配置不删除。
    entry.private_config.setdefault("scheduler", {})["enable"] = False
    _save_timed_section(script_name, section)
    return True


@multi_account_repeat_timed_app.put('/{script_name}/multi_account_repeat_timed/accounts/{account_index}/tasks/{task_name}/enable')
async def set_task_enable(script_name: str, account_index: int, task_name: str, value: str):
    section = _section(script_name)
    entry = _task_entry(_task_account(section, account_index), task_name)
    entry.private_config.setdefault("scheduler", {})["enable"] = _convert_argument("boolean", value)
    _save_timed_section(script_name, section)
    return True


@multi_account_repeat_timed_app.put('/{script_name}/multi_account_repeat_timed/accounts/{account_index}/tasks/{task_name}/quick-schedule')
async def quick_schedule_task(
    script_name: str,
    account_index: int,
    task_name: str,
    run_now: bool = True,
):
    """提供与 OAS 任务行相同的立即执行/立即等待快捷调度。"""
    section = _section(script_name)
    entry = _task_entry(_task_account(section, account_index), task_name)
    scheduler = _entry_scheduler(script_name, entry)
    if scheduler is None:
        raise HTTPException(status_code=400, detail="任务调度器不存在")
    next_run = _scheduler_next_run(scheduler, run_now=run_now)
    entry.next_run = next_run
    entry.private_config.setdefault("scheduler", {})["next_run"] = next_run.strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    _save_timed_section(script_name, section)
    await _broadcast_multi_account_overview(script_name)
    return True


@multi_account_repeat_timed_app.get('/{script_name}/multi_account_repeat_timed/public-args')
async def get_public_args(script_name: str):
    section = _section(script_name)
    return {
        "scheduler": _serialize_group(section.scheduler),
        "multi_account_repeat_timed_config": _serialize_group(section.multi_account_repeat_timed_config),
    }


@multi_account_repeat_timed_app.put('/{script_name}/multi_account_repeat_timed/public-args/{group}/{argument}/value')
async def set_public_arg(script_name: str, group: str, argument: str, types: str, value):
    section = _section(script_name)
    group_name = convert_to_underscore(group)
    if group_name not in {"scheduler", "multi_account_repeat_timed_config"}:
        raise HTTPException(status_code=400, detail="不支持修改该公共配置")
    candidate = copy.deepcopy(section)
    try:
        candidate = model_with_group_overrides(candidate, {
            group_name: {argument: _convert_argument(types, value)},
        })
    except (ValidationError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=f"公共参数无效：{exc}") from exc
    _save_timed_section(script_name, candidate)
    return True


@multi_account_repeat_timed_app.get('/{script_name}/multi_account_repeat_timed/accounts/{account_index}/tasks/{task_name}/args')
async def get_private_args(script_name: str, account_index: int, task_name: str):
    account = _task_account(_section(script_name), account_index)
    entry = _find_task_entry(account, task_name)
    # 未启用任务先显示原生默认值；只有保存时才建立该账号的私有任务项。
    task_args = _default_task_args(
        script_name,
        entry.task_name if entry is not None else task_name,
        remove_scheduler=False,
    )
    if entry is None:
        return task_args
    for group_name, arguments in entry.private_config.items():
        if not isinstance(arguments, dict) or group_name not in task_args:
            continue
        for argument in task_args[group_name]:
            if argument.get("name") in arguments:
                argument["value"] = arguments[argument["name"]]
    return task_args


@multi_account_repeat_timed_app.put('/{script_name}/multi_account_repeat_timed/accounts/{account_index}/tasks/{task_name}/{group}/{argument}/value')
async def set_private_arg(script_name: str, account_index: int, task_name: str, group: str, argument: str, types: str, value):
    section = _section(script_name)
    account = _task_account(section, account_index)
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
    # 定时版以任务项的 next_run 作为调度比较依据；私有调度器修改后同步两处。
    scheduler_changed = convert_to_underscore(group) == "scheduler"
    if scheduler_changed and convert_to_underscore(argument) == "next_run":
        entry.next_run = candidate.scheduler.next_run
    _save_timed_section(script_name, section)
    if scheduler_changed:
        await _broadcast_multi_account_overview(script_name)
    return True


@multi_account_repeat_timed_app.post('/{script_name}/multi_account_repeat_timed/accounts/{account_index}/tasks/{task_name}/private/copy')
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
            # 目标账号未配置此任务时，先创建任务项，默认状态和调度时间不继承源账号。
            target_entry = MultiAccountRepeatTimedTask(task_name=source_entry.task_name)
            target_account.task_list.append(target_entry)
        copied_config = copy.deepcopy(source_entry.private_config)
        # 复制配置不覆盖目标账号原有的下次运行时间。
        scheduler = copied_config.get("scheduler")
        if isinstance(scheduler, dict):
            scheduler["next_run"] = target_entry.next_run.strftime("%Y-%m-%d %H:%M:%S")
        target_entry.private_config = copied_config
        copied_count += 1
    if not copied_count:
        raise HTTPException(status_code=400, detail="没有可复制的目标账号")
    _save_timed_section(script_name, section)
    return True


@multi_account_repeat_timed_app.put('/{script_name}/multi_account_repeat_timed/accounts/{account_index}/tasks/{task_name}/private/default')
async def reset_private_args_to_default(script_name: str, account_index: int, task_name: str):
    section = _section(script_name)
    entry = _find_task_entry(_task_account(section, account_index), task_name)
    # 未启用且从未保存过私有配置时，本来就在使用默认值。
    if entry is None:
        return True
    private = _default_private_config(script_name, entry.task_name)
    # 恢复参数默认值不应改变用户选择的启用状态。
    private.setdefault("scheduler", {})["enable"] = _entry_scheduler_enabled(script_name, entry)
    task_config = getattr(mm.config_cache(script_name).model, convert_to_underscore(entry.task_name), None)
    candidate = task_config.__class__()
    try:
        candidate = model_with_group_overrides(candidate, private)
    except (ValidationError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=f"默认私有参数无效：{exc}") from exc
    entry.private_config = private
    entry.next_run = candidate.scheduler.next_run
    _save_timed_section(script_name, section)
    return True



