import copy
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ValidationError

from module.config.utils import convert_to_underscore
from module.server.api_logger import ApiLoggingRoute
from module.server.main_manager import mm
from tasks.Component.MultiAccount.shared_public_accounts import SharedPublicAccount
from tasks.KekkaiUtilize.config import UtilizeConfig
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


def _save(script_name: str, section) -> None:
    mm.config_cache(script_name).save_selected_fields({"multi_account_kekkai_utilize_new": section})


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


def _set_argument(model: BaseModel, group_name: str, argument_name: str, value) -> None:
    group = _find_group(model, group_name)
    if not isinstance(group, BaseModel):
        raise ValueError(f"找不到参数分组：{group_name}")
    setattr(group, convert_to_underscore(argument_name), value)


def _convert_argument(types: str, value):
    if types == "integer":
        return int(value)
    if types == "number":
        return float(value)
    if types == "boolean":
        return value.lower() in {"true", "1"} if isinstance(value, str) else bool(value)
    return value


def _default_utilize_config() -> dict:
    return {"utilize_config": UtilizeConfig().model_dump()}


def _account_utilize_config(account: MultiAccountKekkaiUtilizeNewAccount) -> UtilizeConfig:
    return UtilizeConfig.model_validate(account.private_config.get("utilize_config", {}))


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
    return {
        "accounts": [
            {
                "index": index,
                "public_account_identifier": account.public_account_identifier,
                "character": account.character,
                "svr": account.svr,
                "next_utilize_time": account.next_utilize_time.isoformat(sep=" ", timespec="seconds"),
                "forbid_period_count": len(account.forbid_periods),
            }
            for index, account in enumerate(_accounts(section), start=1)
        ]
    }


@multi_account_kekkai_utilize_new_app.post('/{script_name}/multi_account_kekkai_utilize_new/accounts')
async def add_account(script_name: str, public_account_identifier: str):
    section = _section(script_name)
    source = _public_account(_library(script_name), public_account_identifier)
    if any(item.public_account_identifier == source.identifier for item in section.account_list):
        return True
    account = MultiAccountKekkaiUtilizeNewAccount()
    account.sync_public_account(source)
    account.private_config = _default_utilize_config()
    section.account_list.append(account)
    _save(script_name, section)
    return True


@multi_account_kekkai_utilize_new_app.delete('/{script_name}/multi_account_kekkai_utilize_new/accounts/{account_index}')
async def delete_account(script_name: str, account_index: int):
    section = _section(script_name)
    section.account_list.remove(_account(section, account_index))
    _save(script_name, section)
    return True


@multi_account_kekkai_utilize_new_app.get('/{script_name}/multi_account_kekkai_utilize_new/public-args')
async def get_public_args(script_name: str):
    section = _section(script_name)
    return {
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
        _set_argument(candidate, group, argument, _convert_argument(types, value))
        candidate = candidate.__class__.model_validate(candidate.model_dump())
    except (ValidationError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=f"任务参数无效：{exc}") from exc
    _save(script_name, candidate)
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
        setattr(candidate, convert_to_underscore(argument), _convert_argument(types, value))
        candidate = UtilizeConfig.model_validate(candidate.model_dump())
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

