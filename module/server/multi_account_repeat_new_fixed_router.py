import copy
import random
import re
from datetime import datetime, time, timedelta
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ValidationError

from module.config.utils import convert_to_underscore, parse_next_server_weekday, parse_tomorrow_server
from module.server.api_logger import ApiLoggingRoute
from module.server.main_manager import mm
from tasks.MultiAccountTaskOrchestration.config import (
    MultiAccountRepeatNewAccount,
    MultiAccountRepeatNewFixedTimeBatch,
    MultiAccountRepeatNewFixedTimeBatchTask,
    MultiAccountRepeatNewTask,
)
from tasks.Component.MultiAccount.shared_public_accounts import SharedPublicAccount
from tasks.Component.config_scheduler import ScheduleMode, Scheduler
from tasks.MultiAccountTaskOrchestration.task_name_resolver import TASK_NAME_ALIASES, TaskNameResolver


multi_account_repeat_new_fixed_app = APIRouter(route_class=ApiLoggingRoute)


def _section(script_name: str):
    section = getattr(mm.config_cache(script_name).model, "multi_account_repeat_new_fixed", None)
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


async def _broadcast_multi_account_overview(script_name: str) -> None:
    """通过当前 OAS WebSocket 立即通知虚拟调度总览重排。"""
    process = mm.script_process.get(script_name)
    if process is not None:
        await process.broadcast_state({
            "multi_account_overview": {"kind": "fixed"},
        })



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

def _normalize_batch_task_names(script_name: str, task_names: str) -> list[str]:
    """校验固定时间批次中的任务；批次只保存内部任务标识。"""
    model = mm.config_cache(script_name).model
    result: list[str] = []
    for raw_name in re.split(r"[,\n]", task_names or ""):
        task_key = convert_to_underscore(raw_name.strip())
        if not task_key:
            continue
        if task_key.startswith("multi_account_repeat") or task_key == "multi_account_task_orchestration":
            raise HTTPException(status_code=400, detail="不能在固定时间批次中嵌套多账号任务")
        if getattr(model, task_key, None) is None or not _has_task_script(task_key):
            raise HTTPException(status_code=400, detail=f"任务不可执行：{raw_name.strip()}")
        if task_key not in result:
            result.append(task_key)
    return result


def _batch(account: MultiAccountRepeatNewAccount, batch_id: str) -> MultiAccountRepeatNewFixedTimeBatch:
    for item in account.fixed_time_batch_list:
        if item.batch_id == batch_id:
            return item
    raise HTTPException(status_code=404, detail="该账号没有此固定时间批次")


def _batch_task_entry(
    batch: MultiAccountRepeatNewFixedTimeBatch,
    task_name: str,
) -> MultiAccountRepeatNewFixedTimeBatchTask:
    """确认任务属于该批次，并返回任务的独立配置项。"""
    entry = batch.task_entry(task_name)
    if entry is None:
        raise HTTPException(status_code=404, detail="该时间批次没有配置此任务")
    return entry


def _apply_private_args(task_args: dict, private: dict) -> dict:
    """将私有覆盖值套到 OASX 参数表单数据中。"""
    for group_name, arguments in private.items():
        if not isinstance(arguments, dict) or group_name not in task_args:
            continue
        for argument in task_args[group_name]:
            if argument.get("name") in arguments:
                argument["value"] = arguments[argument["name"]]
    return task_args


def _scheduler_next_run(scheduler: Scheduler, *, run_now: bool) -> datetime:
    """复用 OAS 原生 Scheduler 的立即执行/立即等待计算方式。"""
    now = datetime.now().replace(microsecond=0)
    if run_now:
        return now - timedelta(days=1)
    next_run = now + scheduler.success_interval
    float_time = scheduler.float_time
    random_float = random.randint(0, float_time.hour * 3600 + float_time.minute * 60 + float_time.second)
    if scheduler.server_update == time(hour=9):
        return next_run + timedelta(seconds=random_float)
    if getattr(scheduler.schedule_mode, "value", scheduler.schedule_mode) == "weekday":
        return parse_next_server_weekday(scheduler.server_update, scheduler.weekdays, random_float)
    return parse_tomorrow_server(scheduler.server_update, scheduler.delay_date, random_float)


def _fixed_batch_target(section) -> datetime | None:
    targets = [
        batch.scheduler.next_run
        for account in section.account_list
        for batch in account.fixed_time_batch_list
        if batch.scheduler.enable and batch.task_names
    ]
    return min(targets) if targets else None


def _refresh_fixed_batch_next_run(batch: MultiAccountRepeatNewFixedTimeBatch, *, now: datetime | None = None, force: bool = False) -> None:
    """新特殊任务直接持有 Scheduler.NextRun；此函数保留为旧调用兼容。"""
    if not batch.scheduler.enable or not batch.task_names:
        return
    if batch.scheduler.next_run.year <= 2023 and force:
        batch.scheduler.next_run = (now or datetime.now()).replace(microsecond=0)


def _refresh_fixed_batch_scheduler(section) -> None:
    target = _fixed_batch_target(section)
    section.scheduler.next_run = (target or (datetime.now() + timedelta(days=1))).replace(microsecond=0)


def _fixed_batch_next_run(account: MultiAccountRepeatNewAccount, batch: MultiAccountRepeatNewFixedTimeBatch, now: datetime | None = None) -> datetime | None:
    if not batch.scheduler.enable or not batch.task_names:
        return None
    return batch.scheduler.next_run


def _fixed_batch_overview_sort_key(account: MultiAccountRepeatNewAccount, batch: MultiAccountRepeatNewFixedTimeBatch, index: int, now: datetime) -> tuple:
    next_run = _fixed_batch_next_run(account, batch, now)
    if next_run is not None and next_run <= now:
        return (0, batch.scheduler.priority, next_run, index)
    return (1, next_run or datetime.max, index)


def _fixed_batch_scheduler(account: MultiAccountRepeatNewAccount, batch: MultiAccountRepeatNewFixedTimeBatch) -> Scheduler:
    return batch.scheduler


def _apply_fixed_batch_scheduler(batch: MultiAccountRepeatNewFixedTimeBatch, scheduler: Scheduler) -> None:
    batch.scheduler = scheduler


def _serialize_fixed_batch(account: MultiAccountRepeatNewAccount, batch: MultiAccountRepeatNewFixedTimeBatch) -> dict:
    now = datetime.now()
    progress_is_today = batch.task_progress_time.date() == now.date()
    completed = set(batch.completed_task_names) if progress_is_today else set()
    failed = set(batch.failed_task_names) if progress_is_today else set()
    unfinished = set(batch.unfinished_task_names) if progress_is_today else set()
    next_run = _fixed_batch_next_run(account, batch, now)
    scheduler = batch.scheduler
    return {
        "batch_id": batch.batch_id,
        "name": batch.name or "固定时间特殊任务",
        "enable": scheduler.enable,
        "run_time": scheduler.server_update.strftime("%H:%M"),
        "schedule_mode": getattr(scheduler.schedule_mode, "value", scheduler.schedule_mode),
        "interval_days": scheduler.delay_date,
        "weekdays": scheduler.weekdays,
        "last_complete_time": batch.last_complete_time.isoformat(sep=" ", timespec="seconds"),
        "task_progress_time": batch.task_progress_time.isoformat(sep=" ", timespec="seconds"),
        "next_run": next_run.isoformat(sep=" ", timespec="seconds") if next_run else None,
        "due": bool(next_run and next_run <= now),
        "schedule_status": "pending" if next_run and next_run <= now else "waiting",
        "priority": scheduler.priority,
        "task_progress": {
            "completed_task_list": str(batch.completed_task_list),
            "failed_task_list": str(batch.failed_task_list),
            "unfinished_task_list": str(batch.unfinished_task_list),
        },
        "tasks": [
            {
                "task_name": task.task_name,
                "task_display_name": _task_display_name(task.task_name),
                "enable": task.enable,
                "has_private_config": bool(task.private_config),
                "status": (
                    "failed" if task.task_name in failed else "unfinished" if task.task_name in unfinished
                    else "completed" if task.task_name in completed else "pending"
                ),
            }
            for task in batch.task_list if task.task_name
        ],
    }


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


def _task_entry(account: MultiAccountRepeatNewAccount, task_name: str) -> MultiAccountRepeatNewTask:
    key = convert_to_underscore(task_name.strip())
    for entry in account.task_list:
        if convert_to_underscore(entry.task_name) == key:
            return entry
    raise HTTPException(status_code=404, detail="账号没有配置该任务")


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


def _set_argument(model: BaseModel, group_name: str, argument_name: str, value) -> None:
    group = _find_group(model, group_name)
    if group is None:
        raise ValueError(f"找不到任务参数分组：{group_name}")
    setattr(group, convert_to_underscore(argument_name), value)


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


@multi_account_repeat_new_fixed_app.get('/{script_name}/multi_account_repeat_new_fixed/accounts')
async def list_task_accounts(script_name: str):
    section = _section(script_name)
    accounts = []
    for index, item in enumerate([entry for entry in section.account_list if entry.public_account_identifier.strip()], start=1):
        accounts.append({
            "index": index,
            "public_account_identifier": item.public_account_identifier,
            "character": item.character,
            "svr": item.svr,
            "account": item.account,
            "account_alias": item.account_alias,
            "apple_or_android": item.apple_or_android,
            # 固定时间版只读取每个时间段内的任务；普通 task_list 仅为旧配置兼容保留。
            "fixed_time_batches": [
                _serialize_fixed_batch(item, batch)
                for _, batch in sorted(
                    enumerate(item.fixed_time_batch_list),
                    key=lambda indexed: _fixed_batch_overview_sort_key(
                        item,
                        indexed[1],
                        indexed[0],
                        datetime.now(),
                    ),
                )
            ],
        })
    # 旧配置首次打开时会补齐各时间段的 Scheduler.NextRun，之后与 OAS 实例一样持久化更新。
    _save(script_name, multi_account_repeat_new_fixed=section)
    return {"accounts": accounts}


@multi_account_repeat_new_fixed_app.post('/{script_name}/multi_account_repeat_new_fixed/accounts')
async def add_task_account(script_name: str, public_account_identifier: str):
    section = _section(script_name)
    source = _public_account(_library(script_name), public_account_identifier)
    if any(item.public_account_identifier == source.identifier for item in section.account_list):
        return True
    account = MultiAccountRepeatNewAccount()
    _sync_task_account(account, source)
    section.account_list = [item for item in section.account_list if item.public_account_identifier.strip()]
    section.account_list.append(account)
    _save(script_name, multi_account_repeat_new_fixed=section)
    return True


@multi_account_repeat_new_fixed_app.delete('/{script_name}/multi_account_repeat_new_fixed/accounts/{account_index}')
async def delete_task_account(script_name: str, account_index: int):
    section = _section(script_name)
    account = _task_account(section, account_index)
    section.account_list.remove(account)
    _save(script_name, multi_account_repeat_new_fixed=section)
    return True


@multi_account_repeat_new_fixed_app.post('/{script_name}/multi_account_repeat_new_fixed/accounts/{account_index}/tasks')
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
    if any(convert_to_underscore(item.task_name) == key for item in account.task_list):
        return True
    account.task_list.append(MultiAccountRepeatNewTask(task_name=key))
    _save(script_name, multi_account_repeat_new_fixed=section)
    return True


@multi_account_repeat_new_fixed_app.delete('/{script_name}/multi_account_repeat_new_fixed/accounts/{account_index}/tasks/{task_name}')
async def delete_task(script_name: str, account_index: int, task_name: str):
    section = _section(script_name)
    account = _task_account(section, account_index)
    entry = _task_entry(account, task_name)
    account.task_list.remove(entry)
    _save(script_name, multi_account_repeat_new_fixed=section)
    return True


@multi_account_repeat_new_fixed_app.get('/{script_name}/multi_account_repeat_new_fixed/fixed-time-tasks')
async def list_fixed_time_tasks(script_name: str):
    """返回所有可执行的普通任务，固定时间任务不再依赖账号普通任务列表。"""
    model = mm.config_cache(script_name).model
    tasks_root = Path.cwd() / "tasks"
    tasks = []
    for directory in tasks_root.iterdir():
        task_key = convert_to_underscore(directory.name)
        if (
            not directory.is_dir()
            or task_key.startswith("multi_account_repeat")
            or task_key == "multi_account_task_orchestration"
            or not (directory / "script_task.py").is_file()
            or getattr(model, task_key, None) is None
        ):
            continue
        tasks.append({
            "task_name": task_key,
            "task_display_name": _task_display_name(task_key),
        })
    tasks.sort(key=lambda item: item["task_display_name"])
    return {"tasks": tasks}


@multi_account_repeat_new_fixed_app.post('/{script_name}/multi_account_repeat_new_fixed/accounts/{account_index}/fixed-time-batches')
async def add_fixed_time_batch(
        script_name: str,
        account_index: int,
        name: str | None = None,
        run_time: str | None = None,
):
    section = _section(script_name)
    account = _task_account(section, account_index)
    batch_name = (name or "").strip() or "固定时间特殊任务"
    if len(batch_name) > 40:
        raise HTTPException(status_code=400, detail="特殊任务名称不能超过 40 个字符")
    scheduler = Scheduler(enable=True)
    if run_time:
        try:
            scheduler.server_update = time.fromisoformat(run_time)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="运行时间格式错误，应为 HH:MM") from exc
    # 与“多账号任务编排”的顺序任务组一致：新建即启用，并按 OAS
    # Scheduler 的立即等待规则生成下一次运行时间，而不是留下 2023 默认值。
    scheduler.next_run = _scheduler_next_run(scheduler, run_now=False)
    batch = MultiAccountRepeatNewFixedTimeBatch(
        batch_id=uuid4().hex,
        name=batch_name,
        scheduler=scheduler,
    )
    account.fixed_time_batch_list.append(batch)
    _refresh_fixed_batch_scheduler(section)
    _save(script_name, multi_account_repeat_new_fixed=section)
    return _serialize_fixed_batch(account, batch)


@multi_account_repeat_new_fixed_app.delete('/{script_name}/multi_account_repeat_new_fixed/accounts/{account_index}/fixed-time-batches/{batch_id}')
async def delete_fixed_time_batch(script_name: str, account_index: int, batch_id: str):
    section = _section(script_name)
    account = _task_account(section, account_index)
    batch = _batch(account, batch_id)
    account.fixed_time_batch_list.remove(batch)
    _refresh_fixed_batch_scheduler(section)
    _save(script_name, multi_account_repeat_new_fixed=section)
    return True


@multi_account_repeat_new_fixed_app.post('/{script_name}/multi_account_repeat_new_fixed/accounts/{account_index}/fixed-time-batches/{batch_id}/copy')
async def copy_fixed_time_batch_to_accounts(
        script_name: str,
        account_index: int,
        batch_id: str,
        target_account_indexes: str,
):
    """复制固定时间批次的周期、任务及批次私有配置，不复制运行记录。"""
    section = _section(script_name)
    source_account = _task_account(section, account_index)
    source_batch = _batch(source_account, batch_id)
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
        # 每个目标账号都创建独立批次，避免批次标识、进度和私有配置互相串用。
        copied_batch = source_batch.model_copy(deep=True)
        copied_batch.batch_id = uuid4().hex
        copied_batch.last_complete_time = datetime(2023, 1, 1)
        copied_batch.task_progress_time = datetime(2023, 1, 1)
        copied_batch.completed_task_list = ""
        copied_batch.failed_task_list = ""
        copied_batch.unfinished_task_list = ""
        copied_batch.scheduler.next_run = _scheduler_next_run(copied_batch.scheduler, run_now=False)
        for task in copied_batch.task_list:
            task.runtime_record = {}
        _refresh_fixed_batch_next_run(copied_batch, force=True)
        target_account.fixed_time_batch_list.append(copied_batch)
        copied_count += 1
    if not copied_count:
        raise HTTPException(status_code=400, detail="没有可复制的目标账号")
    _refresh_fixed_batch_scheduler(section)
    _save(script_name, multi_account_repeat_new_fixed=section)
    return True
@multi_account_repeat_new_fixed_app.put('/{script_name}/multi_account_repeat_new_fixed/accounts/{account_index}/fixed-time-batches/{batch_id}/enable')
async def set_fixed_time_batch_enable(script_name: str, account_index: int, batch_id: str, value: str):
    section = _section(script_name)
    batch = _batch(_task_account(section, account_index), batch_id)
    batch.scheduler.enable = _convert_argument("boolean", value)
    _refresh_fixed_batch_scheduler(section)
    _save(script_name, multi_account_repeat_new_fixed=section)
    return True


@multi_account_repeat_new_fixed_app.put('/{script_name}/multi_account_repeat_new_fixed/accounts/{account_index}/fixed-time-batches/{batch_id}/run-time')
async def set_fixed_time_batch_run_time(script_name: str, account_index: int, batch_id: str, value: str):
    section = _section(script_name)
    try:
        batch = _batch(_task_account(section, account_index), batch_id)
        batch.scheduler.server_update = time.fromisoformat(value)
        batch.scheduler.next_run = _scheduler_next_run(batch.scheduler, run_now=False)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="运行时间格式错误，应为 HH:MM") from exc
    _refresh_fixed_batch_scheduler(section)
    _save(script_name, multi_account_repeat_new_fixed=section)
    return True


@multi_account_repeat_new_fixed_app.put('/{script_name}/multi_account_repeat_new_fixed/accounts/{account_index}/fixed-time-batches/{batch_id}/schedule')
async def set_fixed_time_batch_schedule(
        script_name: str,
        account_index: int,
        batch_id: str,
        run_time: str,
        schedule_mode: str = "daily",
        interval_days: int = 1,
        weekdays: str = "",
):
    """一次性更新时间段的时间和运行周期。"""
    section = _section(script_name)
    try:
        parsed_time = time.fromisoformat(run_time)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="运行时间格式错误，应为 HH:MM") from exc
    mode = schedule_mode.strip().lower()
    if mode not in {"daily", "interval", "weekday"}:
        raise HTTPException(status_code=400, detail="运行周期只能是每天、间隔天数或指定星期")
    if not 1 <= interval_days <= 365:
        raise HTTPException(status_code=400, detail="间隔天数必须在 1 到 365 之间")
    try:
        parsed_weekdays = sorted({
            int(item.strip())
            for item in weekdays.split(",")
            if item.strip()
        })
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="星期格式错误") from exc
    if any(day < 1 or day > 7 for day in parsed_weekdays):
        raise HTTPException(status_code=400, detail="星期只能选择周一到周日")
    if mode == "weekday" and not parsed_weekdays:
        raise HTTPException(status_code=400, detail="指定星期至少选择一天")

    batch = _batch(_task_account(section, account_index), batch_id)
    scheduler = batch.scheduler
    scheduler.server_update = parsed_time
    scheduler.schedule_mode = ScheduleMode.WEEKDAY if mode == "weekday" else ScheduleMode.INTERVAL
    scheduler.delay_date = interval_days
    scheduler.weekdays = parsed_weekdays or list(range(1, 8))
    scheduler.next_run = _scheduler_next_run(scheduler, run_now=False)
    _refresh_fixed_batch_scheduler(section)
    _save(script_name, multi_account_repeat_new_fixed=section)
    return True


@multi_account_repeat_new_fixed_app.get('/{script_name}/multi_account_repeat_new_fixed/accounts/{account_index}/fixed-time-batches/{batch_id}/scheduler/args')
async def get_fixed_time_batch_scheduler_args(script_name: str, account_index: int, batch_id: str):
    """直接返回特殊任务持有的原生 OAS Scheduler 表单数据。"""
    account = _task_account(_section(script_name), account_index)
    batch = _batch(account, batch_id)
    # 固定时间段完整复用 OAS 原生 Scheduler 的所有字段。
    allowed = {"enable", "next_run", "priority", "success_interval", "failure_interval", "server_update", "schedule_mode", "delay_date", "weekdays", "float_time"}
    arguments = [
        item for item in _serialize_group(_fixed_batch_scheduler(account, batch))
        if item.get("name") in allowed
    ]
    return {"scheduler": arguments}


@multi_account_repeat_new_fixed_app.put('/{script_name}/multi_account_repeat_new_fixed/accounts/{account_index}/fixed-time-batches/{batch_id}/scheduler/{argument}/value')
async def set_fixed_time_batch_scheduler_arg(
    script_name: str,
    account_index: int,
    batch_id: str,
    argument: str,
    types: str,
    value,
):
    """使用 OAS 原生 Scheduler 的校验规则更新一个时间段。"""
    section = _section(script_name)
    account = _task_account(section, account_index)
    batch = _batch(account, batch_id)
    argument_name = convert_to_underscore(argument)
    allowed = {"enable", "next_run", "priority", "success_interval", "failure_interval", "server_update", "schedule_mode", "delay_date", "weekdays", "float_time"}
    if argument_name not in allowed:
        raise HTTPException(status_code=400, detail="该时间段不支持修改此调度器参数")
    try:
        scheduler_data = _fixed_batch_scheduler(account, batch).model_dump(mode="json")
        scheduler_data[argument_name] = _convert_argument(types, value)
        scheduler = Scheduler.model_validate(scheduler_data)
        _apply_fixed_batch_scheduler(batch, scheduler)
    except (ValidationError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=f"特殊任务调度器参数无效：{exc}") from exc
    _refresh_fixed_batch_scheduler(section)
    _save(script_name, multi_account_repeat_new_fixed=section)
    await _broadcast_multi_account_overview(script_name)
    return True


@multi_account_repeat_new_fixed_app.put('/{script_name}/multi_account_repeat_new_fixed/accounts/{account_index}/fixed-time-batches/{batch_id}/quick-schedule')
async def quick_schedule_fixed_time_batch(script_name: str, account_index: int, batch_id: str, run_now: bool = True):
    section = _section(script_name)
    batch = _batch(_task_account(section, account_index), batch_id)
    batch.scheduler.next_run = _scheduler_next_run(batch.scheduler, run_now=run_now)
    _refresh_fixed_batch_scheduler(section)
    _save(script_name, multi_account_repeat_new_fixed=section)
    await _broadcast_multi_account_overview(script_name)
    return True


@multi_account_repeat_new_fixed_app.post('/{script_name}/multi_account_repeat_new_fixed/accounts/{account_index}/fixed-time-batches/{batch_id}/rerun')
async def rerun_fixed_time_batch(script_name: str, account_index: int, batch_id: str):
    section = _section(script_name)
    batch = _batch(_task_account(section, account_index), batch_id)
    batch.last_complete_time = datetime(2023, 1, 1)
    batch.task_progress_time = datetime.now()
    batch.completed_task_list = ""
    batch.failed_task_list = ""
    batch.unfinished_task_list = ""
    _save(script_name, multi_account_repeat_new_fixed=section)
    return True


@multi_account_repeat_new_fixed_app.put('/{script_name}/multi_account_repeat_new_fixed/accounts/{account_index}/fixed-time-batches/{batch_id}/last-complete-time')
async def set_fixed_time_batch_last_complete_time(script_name: str, account_index: int, batch_id: str, value: str):
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.replace(tzinfo=None)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="完成时间格式错误，应为 YYYY-MM-DD HH:MM:SS") from exc
    section = _section(script_name)
    _batch(_task_account(section, account_index), batch_id).last_complete_time = parsed
    _save(script_name, multi_account_repeat_new_fixed=section)
    return True


@multi_account_repeat_new_fixed_app.put('/{script_name}/multi_account_repeat_new_fixed/accounts/{account_index}/fixed-time-batches/{batch_id}/tasks/order')
async def reorder_fixed_time_batch_tasks(script_name: str, account_index: int, batch_id: str, task_names: str):
    section = _section(script_name)
    batch = _batch(_task_account(section, account_index), batch_id)
    enabled = [item for item in batch.task_list if item.enable]
    requested = [convert_to_underscore(name.strip()) for name in re.split(r"[,\n]", task_names) if name.strip()]
    enabled_names = [convert_to_underscore(item.task_name) for item in enabled]
    if len(requested) != len(enabled_names) or len(set(requested)) != len(requested) or set(requested) != set(enabled_names):
        raise HTTPException(status_code=400, detail="排序任务必须与当前已启用任务完全一致")
    by_name = {convert_to_underscore(item.task_name): item for item in enabled}
    batch.task_list = [*(by_name[name] for name in requested), *(item for item in batch.task_list if not item.enable)]
    _save(script_name, multi_account_repeat_new_fixed=section)
    return True


@multi_account_repeat_new_fixed_app.put('/{script_name}/multi_account_repeat_new_fixed/accounts/{account_index}/fixed-time-batches/{batch_id}/tasks/{task_name}/status')
async def set_fixed_time_batch_task_status(script_name: str, account_index: int, batch_id: str, task_name: str, value: str):
    section = _section(script_name)
    batch = _batch(_task_account(section, account_index), batch_id)
    entry = _batch_task_entry(batch, task_name)
    status = value.strip().lower()
    if status not in {"completed", "failed", "unfinished", "pending"}:
        raise HTTPException(status_code=400, detail="任务状态只能是 completed、failed、unfinished 或 pending")
    key = convert_to_underscore(entry.task_name)
    completed = [name for name in batch.completed_task_names if name != key]
    failed = [name for name in batch.failed_task_names if name != key]
    unfinished = [name for name in batch.unfinished_task_names if name != key]
    if status == "completed":
        completed.append(key)
    elif status == "failed":
        failed.append(key)
    elif status == "unfinished":
        unfinished.append(key)
    elif batch.last_complete_time.date() == datetime.now().date():
        batch.last_complete_time = datetime(2023, 1, 1)
    batch.task_progress_time = datetime.now()
    batch.completed_task_list = "\n".join(_task_display_name(name) for name in completed)
    batch.failed_task_list = "\n".join(_task_display_name(name) for name in failed)
    batch.unfinished_task_list = "\n".join(_task_display_name(name) for name in unfinished)
    _save(script_name, multi_account_repeat_new_fixed=section)
    return True


@multi_account_repeat_new_fixed_app.post('/{script_name}/multi_account_repeat_new_fixed/accounts/{account_index}/fixed-time-batches/{batch_id}/tasks')
async def add_fixed_time_batch_task(script_name: str, account_index: int, batch_id: str, task_name: str):
    section = _section(script_name)
    account = _task_account(section, account_index)
    batch = _batch(account, batch_id)
    normalized = _normalize_batch_task_names(script_name, task_name)
    if not normalized:
        raise HTTPException(status_code=400, detail="请选择要添加的任务")
    task_key = normalized[0]
    entry = batch.task_entry(task_key)
    if entry is None:
        batch.task_list.append(MultiAccountRepeatNewFixedTimeBatchTask(task_name=task_key))
    else:
        # 重新添加只恢复启用状态，保留之前停用时留下的私有配置和运行记录。
        entry.enable = True
    _refresh_fixed_batch_next_run(batch, force=True)
    _refresh_fixed_batch_scheduler(section)
    _save(script_name, multi_account_repeat_new_fixed=section)
    return True


@multi_account_repeat_new_fixed_app.put('/{script_name}/multi_account_repeat_new_fixed/accounts/{account_index}/fixed-time-batches/{batch_id}/tasks/{task_name}/enable')
async def set_fixed_time_batch_task_enable(
    script_name: str,
    account_index: int,
    batch_id: str,
    task_name: str,
    value: str,
):
    """停用或恢复时间段任务，不删除其私有配置和运行记录。"""
    section = _section(script_name)
    account = _task_account(section, account_index)
    batch = _batch(account, batch_id)
    entry = _batch_task_entry(batch, task_name)
    entry.enable = _convert_argument("boolean", value)
    _refresh_fixed_batch_next_run(batch, force=True)
    _refresh_fixed_batch_scheduler(section)
    _save(script_name, multi_account_repeat_new_fixed=section)
    return True


@multi_account_repeat_new_fixed_app.delete('/{script_name}/multi_account_repeat_new_fixed/accounts/{account_index}/fixed-time-batches/{batch_id}/tasks/{task_name}')
async def delete_fixed_time_batch_task(script_name: str, account_index: int, batch_id: str, task_name: str):
    section = _section(script_name)
    account = _task_account(section, account_index)
    batch = _batch(account, batch_id)
    entry = _batch_task_entry(batch, task_name)
    # OAS 左滑停用语义：不删除任务私有配置，仅取消当前时间段的启用状态。
    entry.enable = False
    _refresh_fixed_batch_next_run(batch, force=True)
    _refresh_fixed_batch_scheduler(section)
    _save(script_name, multi_account_repeat_new_fixed=section)
    return True


@multi_account_repeat_new_fixed_app.get('/{script_name}/multi_account_repeat_new_fixed/accounts/{account_index}/fixed-time-batches/{batch_id}/tasks/{task_name}/args')
async def get_fixed_time_batch_task_args(script_name: str, account_index: int, batch_id: str, task_name: str):
    account = _task_account(_section(script_name), account_index)
    entry = _batch_task_entry(_batch(account, batch_id), task_name)
    task_args = _default_task_args(script_name, entry.task_name, remove_scheduler=True)
    return _apply_private_args(task_args, entry.private_config)


@multi_account_repeat_new_fixed_app.put('/{script_name}/multi_account_repeat_new_fixed/accounts/{account_index}/fixed-time-batches/{batch_id}/tasks/{task_name}/{group}/{argument}/value')
async def set_fixed_time_batch_task_arg(
        script_name: str, account_index: int, batch_id: str, task_name: str,
        group: str, argument: str, types: str, value,
):
    section = _section(script_name)
    account = _task_account(section, account_index)
    entry = _batch_task_entry(_batch(account, batch_id), task_name)
    if convert_to_underscore(group) == "scheduler":
        raise HTTPException(status_code=400, detail="不能在批次私有配置中修改调度参数")
    task_config = getattr(mm.config_cache(script_name).model, convert_to_underscore(entry.task_name), None)
    if not isinstance(task_config, BaseModel):
        raise HTTPException(status_code=400, detail="任务配置不存在")

    private = copy.deepcopy(entry.private_config)
    private.setdefault(convert_to_underscore(group), {})[convert_to_underscore(argument)] = _convert_argument(types, value)
    candidate = task_config.__class__()
    try:
        for group_name, arguments in private.items():
            if isinstance(arguments, dict):
                for name, item_value in arguments.items():
                    _set_argument(candidate, group_name, name, item_value)
        candidate.__class__.model_validate(candidate.model_dump())
    except (ValidationError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=f"批次私有参数无效：{exc}") from exc
    entry.private_config = private
    _save(script_name, multi_account_repeat_new_fixed=section)
    return True


@multi_account_repeat_new_fixed_app.put('/{script_name}/multi_account_repeat_new_fixed/accounts/{account_index}/fixed-time-batches/{batch_id}/tasks/{task_name}/private/default')
async def reset_fixed_time_batch_task_private_config_to_default(script_name: str, account_index: int, batch_id: str, task_name: str):
    # 恢复任务模型的默认值，而不是清空后继续继承公共配置。
    section = _section(script_name)
    account = _task_account(section, account_index)
    entry = _batch_task_entry(_batch(account, batch_id), task_name)
    entry.private_config = _default_private_config(script_name, entry.task_name)
    _save(script_name, multi_account_repeat_new_fixed=section)
    return True


@multi_account_repeat_new_fixed_app.get('/{script_name}/multi_account_repeat_new_fixed/public-args')
async def get_public_args(script_name: str):
    section = _section(script_name)
    return {
        "scheduler": _serialize_group(section.scheduler),
        "multi_account_repeat_new_config": _serialize_group(section.multi_account_repeat_new_config),
    }


@multi_account_repeat_new_fixed_app.put('/{script_name}/multi_account_repeat_new_fixed/public-args/{group}/{argument}/value')
async def set_public_arg(script_name: str, group: str, argument: str, types: str, value):
    section = _section(script_name)
    group_name = convert_to_underscore(group)
    if group_name not in {"scheduler", "multi_account_repeat_new_config"}:
        raise HTTPException(status_code=400, detail="不支持修改该公共配置")
    candidate = copy.deepcopy(section)
    try:
        _set_argument(candidate, group_name, argument, _convert_argument(types, value))
        candidate = candidate.__class__.model_validate(candidate.model_dump())
    except (ValidationError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=f"公共参数无效：{exc}") from exc
    _save(script_name, multi_account_repeat_new_fixed=candidate)
    return True


@multi_account_repeat_new_fixed_app.get('/{script_name}/multi_account_repeat_new_fixed/accounts/{account_index}/tasks/{task_name}/args')
async def get_private_args(script_name: str, account_index: int, task_name: str):
    account = _task_account(_section(script_name), account_index)
    entry = _task_entry(account, task_name)
    task_args = _default_task_args(script_name, entry.task_name, remove_scheduler=True)
    return _apply_private_args(task_args, entry.private_config)


@multi_account_repeat_new_fixed_app.put('/{script_name}/multi_account_repeat_new_fixed/accounts/{account_index}/tasks/{task_name}/{group}/{argument}/value')
async def set_private_arg(script_name: str, account_index: int, task_name: str, group: str, argument: str, types: str, value):
    section = _section(script_name)
    account = _task_account(section, account_index)
    entry = _task_entry(account, task_name)
    if convert_to_underscore(group) == "scheduler":
        raise HTTPException(status_code=400, detail="不能在私有配置中修改调度参数")
    task_config = getattr(mm.config_cache(script_name).model, convert_to_underscore(entry.task_name), None)
    if not isinstance(task_config, BaseModel):
        raise HTTPException(status_code=400, detail="任务配置不存在")
    private = copy.deepcopy(entry.private_config)
    private.setdefault(convert_to_underscore(group), {})[convert_to_underscore(argument)] = _convert_argument(types, value)
    candidate = task_config.__class__()
    try:
        for group_name, arguments in private.items():
            if isinstance(arguments, dict):
                for name, item_value in arguments.items():
                    _set_argument(candidate, group_name, name, item_value)
        candidate.__class__.model_validate(candidate.model_dump())
    except (ValidationError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=f"私有参数无效：{exc}") from exc
    entry.private_config = private
    _save(script_name, multi_account_repeat_new_fixed=section)
    return True


@multi_account_repeat_new_fixed_app.post('/{script_name}/multi_account_repeat_new_fixed/accounts/{account_index}/tasks/{task_name}/private/copy')
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
    _save(script_name, multi_account_repeat_new_fixed=section)
    return True


@multi_account_repeat_new_fixed_app.put('/{script_name}/multi_account_repeat_new_fixed/accounts/{account_index}/tasks/{task_name}/private/default')
async def reset_private_args_to_default(script_name: str, account_index: int, task_name: str):
    section = _section(script_name)
    entry = _task_entry(_task_account(section, account_index), task_name)
    private = _default_private_config(script_name, entry.task_name)
    task_config = getattr(mm.config_cache(script_name).model, convert_to_underscore(entry.task_name), None)
    candidate = task_config.__class__()
    try:
        for group_name, arguments in private.items():
            for name, value in arguments.items():
                _set_argument(candidate, group_name, name, value)
        candidate.__class__.model_validate(candidate.model_dump())
    except (ValidationError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=f"默认私有参数无效：{exc}") from exc
    entry.private_config = private
    _save(script_name, multi_account_repeat_new_fixed=section)
    return True



