import copy
import random
from datetime import datetime, time, timedelta
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ValidationError

from module.config.model_overrides import model_with_field_overrides, model_with_group_overrides
from module.config.utils import convert_to_underscore
from module.server.api_logger import ApiLoggingRoute
from module.server.main_manager import mm
from tasks.Component.MultiAccount.shared_public_accounts import SharedPublicAccount
from module.config.utils import parse_next_server_weekday, parse_tomorrow_server
from tasks.KekkaiUtilize.config import UtilizeConfig, UtilizeScheduler
from tasks.MultiAccountKekkaiUtilizeNew.config import (
    MultiAccountKekkaiUtilizeNewAccount,
    MultiAccountKekkaiUtilizeNewForbidPeriod,
)


multi_account_kekkai_utilize_new_app = APIRouter(route_class=ApiLoggingRoute)


def _section(script_name: str):
    section = getattr(mm.config_cache(script_name).model, "multi_account_kekkai_utilize_new", None)
    if section is None:
        raise HTTPException(status_code=404, detail="当前配置没有多账号多任务蹭卡新")
    return section


def _library(script_name: str):
    library = getattr(mm.config_cache(script_name).model, "multi_account_shared_accounts", None)
    if library is None:
        raise HTTPException(status_code=404, detail="当前配置没有公共账号库")
    return library


def _refresh_outer_scheduler(section) -> None:
    """外层 OAS 任务只负责唤醒最近到期的账号虚拟蹭卡任务。"""
    next_runs = [
        account.scheduler.next_run
        for account in _accounts(section)
        if account.is_valid() and account.scheduler.enable
    ]
    section.scheduler.next_run = (
        min(next_runs).replace(microsecond=0)
        if next_runs
        else datetime.max.replace(microsecond=0)
    )


def _save(script_name: str, section) -> None:
    _refresh_outer_scheduler(section)
    mm.config_cache(script_name).save_selected_fields({"multi_account_kekkai_utilize_new": section})


async def _broadcast_overview(script_name: str) -> None:
    """沿用 OAS WebSocket，通知所有页面重新拉取并按虚拟调度顺序显示。"""
    process = mm.script_process.get(script_name)
    if process is not None:
        await process.broadcast_state({
            "multi_account_overview": {"kind": "utilize"},
        })


def _public_account(library, identifier: str) -> SharedPublicAccount:
    account = library.find(identifier)
    if account is None:
        raise HTTPException(status_code=404, detail="公共账号不存在")
    return account


def _accounts(section) -> list[MultiAccountKekkaiUtilizeNewAccount]:
    return [item for item in section.account_list if item.public_account_identifier.strip()]


def _account(section, index: int) -> MultiAccountKekkaiUtilizeNewAccount:
    accounts = _accounts(section)
    if index < 1 or index > len(accounts):
        raise HTTPException(status_code=404, detail="运行账号不存在")
    return accounts[index - 1]


def _serialize_group(group: BaseModel) -> list[dict]:
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


def _find_group(model: BaseModel, group_name: str):
    return getattr(model, convert_to_underscore(group_name), None)


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


def _default_utilize_config() -> dict:
    return {"utilize_config": UtilizeConfig().model_dump()}


def _account_utilize_config(account: MultiAccountKekkaiUtilizeNewAccount) -> UtilizeConfig:
    return UtilizeConfig.model_validate(account.private_config.get("utilize_config", {}))


def _validated_model_value(model: BaseModel, argument: str, value):
    """将 API 原始值先合并到字典，再交给 Pydantic 转换为字段实际类型。"""
    return model_with_field_overrides(model, {argument: value})


def _account_scheduler(account: MultiAccountKekkaiUtilizeNewAccount) -> UtilizeScheduler:
    return account.scheduler


def _scheduler_next_run(scheduler: UtilizeScheduler, *, run_now: bool) -> datetime:
    """与 OAS quick run / quick wait 一致地安排虚拟账号任务。"""
    now = datetime.now().replace(microsecond=0)
    if run_now:
        return now - timedelta(days=1)
    interval = scheduler.success_interval
    next_run = now + interval
    float_time = scheduler.float_time
    random_float = random.randint(0, float_time.hour * 3600 + float_time.minute * 60 + float_time.second)
    if scheduler.server_update == time(hour=9):
        return next_run + timedelta(seconds=random_float)
    if getattr(scheduler.schedule_mode, "value", scheduler.schedule_mode) == "weekday":
        return parse_next_server_weekday(scheduler.server_update, scheduler.weekdays, random_float)
    return parse_tomorrow_server(scheduler.server_update, scheduler.delay_date, random_float)


def _overview_sort_key(row: dict, index: int) -> tuple:
    if row["schedule_status"] == "pending":
        return (0, row["priority"], row["next_run_value"], index)
    return (1, row["next_run_value"], index)


def _parse_target_indexes(raw: str) -> set[int]:
    try:
        result = {int(item.strip()) for item in raw.split(",") if item.strip()}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="目标账号序号格式错误") from exc
    if not result:
        raise HTTPException(status_code=400, detail="请至少选择一个目标账号")
    return result


@multi_account_kekkai_utilize_new_app.get('/{script_name}/multi_account_kekkai_utilize_new/public-accounts')
async def list_public_accounts(script_name: str):
    library = _library(script_name)
    return {
        "accounts": [
            {
                "identifier": item.identifier,
                "character": item.character,
                "svr": item.svr,
                "account": item.account,
                "apple_or_android": item.apple_or_android,
            }
            for item in library.account_list
            if item.identifier.strip()
        ]
    }


@multi_account_kekkai_utilize_new_app.get('/{script_name}/multi_account_kekkai_utilize_new/accounts')
async def list_accounts(script_name: str):
    section = _section(script_name)
    now = datetime.now()
    rows = []
    for index, account in enumerate(_accounts(section), start=1):
        scheduler = _account_scheduler(account)
        next_run = scheduler.next_run
        rows.append({
            "index": index,
            "public_account_identifier": account.public_account_identifier,
            "character": account.character,
            "svr": account.svr,
            "enabled": scheduler.enable,
            "next_run": next_run.isoformat(sep=" ", timespec="seconds"),
            "next_utilize_time": next_run.isoformat(sep=" ", timespec="seconds"),
            "priority": scheduler.priority,
            "schedule_status": "pending" if scheduler.enable and next_run <= now else "waiting",
            "forbid_period_count": len(account.forbid_periods),
            "_next_run_value": next_run,
        })
    rows.sort(key=lambda row: _overview_sort_key({**row, "next_run_value": row["_next_run_value"]}, row["index"]))
    for row in rows:
        row.pop("priority", None)
        row.pop("_next_run_value", None)
    return {"accounts": rows}


@multi_account_kekkai_utilize_new_app.post('/{script_name}/multi_account_kekkai_utilize_new/accounts')
async def add_account(script_name: str, public_account_identifier: str):
    section = _section(script_name)
    source = _public_account(_library(script_name), public_account_identifier)
    if any(item.public_account_identifier == source.identifier for item in section.account_list):
        return True
    account = MultiAccountKekkaiUtilizeNewAccount()
    account.sync_public_account(source)
    account.scheduler.enable = True
    account.next_utilize_time = account.scheduler.next_run
    account.private_config = _default_utilize_config()
    section.account_list.append(account)
    _save(script_name, section)
    await _broadcast_overview(script_name)
    return True


@multi_account_kekkai_utilize_new_app.delete('/{script_name}/multi_account_kekkai_utilize_new/accounts/{account_index}')
async def delete_account(script_name: str, account_index: int):
    section = _section(script_name)
    section.account_list.remove(_account(section, account_index))
    _save(script_name, section)
    await _broadcast_overview(script_name)
    return True


@multi_account_kekkai_utilize_new_app.get('/{script_name}/multi_account_kekkai_utilize_new/public-args')
async def get_public_args(script_name: str):
    section = _section(script_name)
    return {
        # 保留外层 OAS 调度器：控制该功能本身的启用与总优先级；
        # 账号行各自的 Scheduler 决定具体蹭卡时间。
        "scheduler": _serialize_group(section.scheduler),
        "multi_account_kekkai_utilize_new_config": _serialize_group(
            section.multi_account_kekkai_utilize_new_config
        ),
    }


@multi_account_kekkai_utilize_new_app.put('/{script_name}/multi_account_kekkai_utilize_new/public-args/{group}/{argument}/value')
async def set_public_arg(script_name: str, group: str, argument: str, types: str, value):
    section = _section(script_name)
    allowed = {"scheduler", "multi_account_kekkai_utilize_new_config"}
    if convert_to_underscore(group) not in allowed:
        raise HTTPException(status_code=400, detail="不支持修改该任务配置")
    candidate = copy.deepcopy(section)
    try:
        candidate = model_with_group_overrides(candidate, {
            group: {argument: _convert_argument(types, value)},
        })
    except (ValidationError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=f"任务参数无效：{exc}") from exc
    _save(script_name, candidate)
    return True


@multi_account_kekkai_utilize_new_app.get('/{script_name}/multi_account_kekkai_utilize_new/accounts/{account_index}/scheduler-args')
async def get_account_scheduler_args(script_name: str, account_index: int):
    return {"scheduler": _serialize_group(_account_scheduler(_account(_section(script_name), account_index)))}


@multi_account_kekkai_utilize_new_app.put('/{script_name}/multi_account_kekkai_utilize_new/accounts/{account_index}/scheduler-args/{argument}/value')
async def set_account_scheduler_arg(script_name: str, account_index: int, argument: str, types: str, value):
    section = _section(script_name)
    account = _account(section, account_index)
    candidate = copy.deepcopy(_account_scheduler(account))
    try:
        candidate = model_with_field_overrides(
            candidate,
            {argument: _convert_argument(types, value)},
        )
    except (ValidationError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=f"账号蹭卡调度器无效：{exc}") from exc
    account.scheduler = candidate
    account.next_utilize_time = candidate.next_run
    _save(script_name, section)
    await _broadcast_overview(script_name)
    return True


@multi_account_kekkai_utilize_new_app.put('/{script_name}/multi_account_kekkai_utilize_new/accounts/{account_index}/enable')
async def set_account_enable(script_name: str, account_index: int, value: str):
    section = _section(script_name)
    account = _account(section, account_index)
    account.scheduler.enable = _convert_argument("boolean", value)
    _save(script_name, section)
    await _broadcast_overview(script_name)
    return True


@multi_account_kekkai_utilize_new_app.put('/{script_name}/multi_account_kekkai_utilize_new/accounts/{account_index}/quick-schedule')
async def quick_schedule_account(script_name: str, account_index: int, run_now: bool = True):
    section = _section(script_name)
    account = _account(section, account_index)
    account.scheduler.next_run = _scheduler_next_run(account.scheduler, run_now=run_now)
    account.next_utilize_time = account.scheduler.next_run
    _save(script_name, section)
    await _broadcast_overview(script_name)
    return True


@multi_account_kekkai_utilize_new_app.get('/{script_name}/multi_account_kekkai_utilize_new/accounts/{account_index}/utilize-args')
async def get_account_utilize_args(script_name: str, account_index: int):
    account = _account(_section(script_name), account_index)
    return {"utilize_config": _serialize_group(_account_utilize_config(account))}


@multi_account_kekkai_utilize_new_app.put('/{script_name}/multi_account_kekkai_utilize_new/accounts/{account_index}/utilize-args/{argument}/value')
async def set_account_utilize_arg(script_name: str, account_index: int, argument: str, types: str, value):
    section = _section(script_name)
    account = _account(section, account_index)
    candidate = _account_utilize_config(account)
    try:
        candidate = _validated_model_value(
            candidate,
            argument,
            _convert_argument(types, value),
        )
    except (ValidationError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=f"账号蹭卡配置无效：{exc}") from exc
    account.private_config = {"utilize_config": candidate.model_dump()}
    _save(script_name, section)
    return True


@multi_account_kekkai_utilize_new_app.put('/{script_name}/multi_account_kekkai_utilize_new/accounts/{account_index}/utilize-args/default')
async def reset_account_utilize_args(script_name: str, account_index: int):
    section = _section(script_name)
    _account(section, account_index).private_config = _default_utilize_config()
    _save(script_name, section)
    return True


@multi_account_kekkai_utilize_new_app.post('/{script_name}/multi_account_kekkai_utilize_new/accounts/{account_index}/utilize-args/copy')
async def copy_account_utilize_args(script_name: str, account_index: int, target_account_indexes: str):
    section = _section(script_name)
    source = _account(section, account_index)
    targets = _parse_target_indexes(target_account_indexes)
    copied = 0
    for target_index in targets:
        if target_index == account_index:
            continue
        _account(section, target_index).private_config = copy.deepcopy(source.private_config)
        copied += 1
    if not copied:
        raise HTTPException(status_code=400, detail="没有可复制的目标账号")
    _save(script_name, section)
    return True


@multi_account_kekkai_utilize_new_app.get('/{script_name}/multi_account_kekkai_utilize_new/accounts/{account_index}/forbid-periods')
async def get_forbid_periods(script_name: str, account_index: int):
    account = _account(_section(script_name), account_index)
    return {
        "periods": [
            {"index": index, "start": period.start.isoformat(), "end": period.end.isoformat()}
            for index, period in enumerate(account.forbid_periods, start=1)
        ]
    }


@multi_account_kekkai_utilize_new_app.post('/{script_name}/multi_account_kekkai_utilize_new/accounts/{account_index}/forbid-periods')
async def add_forbid_period(script_name: str, account_index: int):
    section = _section(script_name)
    _account(section, account_index).forbid_periods.append(MultiAccountKekkaiUtilizeNewForbidPeriod())
    _save(script_name, section)
    return True


@multi_account_kekkai_utilize_new_app.put('/{script_name}/multi_account_kekkai_utilize_new/accounts/{account_index}/forbid-periods/{period_index}')
async def update_forbid_period(script_name: str, account_index: int, period_index: int, start: str, end: str):
    section = _section(script_name)
    account = _account(section, account_index)
    if period_index < 1 or period_index > len(account.forbid_periods):
        raise HTTPException(status_code=404, detail="禁止时段不存在")
    try:
        account.forbid_periods[period_index - 1] = MultiAccountKekkaiUtilizeNewForbidPeriod(
            start=start, end=end
        )
    except (ValidationError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=f"禁止时段格式无效：{exc}") from exc
    _save(script_name, section)
    return True


@multi_account_kekkai_utilize_new_app.delete('/{script_name}/multi_account_kekkai_utilize_new/accounts/{account_index}/forbid-periods/{period_index}')
async def delete_forbid_period(script_name: str, account_index: int, period_index: int):
    section = _section(script_name)
    account = _account(section, account_index)
    if period_index < 1 or period_index > len(account.forbid_periods):
        raise HTTPException(status_code=404, detail="禁止时段不存在")
    account.forbid_periods.pop(period_index - 1)
    _save(script_name, section)
    return True


@multi_account_kekkai_utilize_new_app.put('/{script_name}/multi_account_kekkai_utilize_new/accounts/{account_index}/forbid-periods/default')
async def reset_forbid_periods(script_name: str, account_index: int):
    section = _section(script_name)
    _account(section, account_index).forbid_periods = []
    _save(script_name, section)
    return True


@multi_account_kekkai_utilize_new_app.post('/{script_name}/multi_account_kekkai_utilize_new/accounts/{account_index}/forbid-periods/copy')
async def copy_forbid_periods(script_name: str, account_index: int, target_account_indexes: str):
    section = _section(script_name)
    source = _account(section, account_index)
    targets = _parse_target_indexes(target_account_indexes)
    copied = 0
    for target_index in targets:
        if target_index == account_index:
            continue
        _account(section, target_index).forbid_periods = copy.deepcopy(source.forbid_periods)
        copied += 1
    if not copied:
        raise HTTPException(status_code=400, detail="没有可复制的目标账号")
    _save(script_name, section)
    return True

