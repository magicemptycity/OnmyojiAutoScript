import copy
import random
from datetime import datetime, time, timedelta
from dataclasses import dataclass
from typing import Any, Type

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ValidationError

from module.config.model_overrides import model_with_field_overrides, model_with_group_overrides
from module.config.utils import convert_to_underscore
from module.server.api_logger import ApiLoggingRoute
from module.server.main_manager import mm
from module.server.multi_account_feature_registry import MultiAccountFeature
from tasks.Component.MultiAccount.shared_public_accounts import SharedPublicAccount
from module.config.utils import parse_next_server_weekday, parse_tomorrow_server

@dataclass(frozen=True)
class AccountSchedulerRouterSpec:
    feature: MultiAccountFeature
    account_model: Type[Any]
    settings_model: Type[BaseModel]
    public_config_attr: str
    public_task_attr: str
    period_model: Type[Any] | None = None

    def __post_init__(self) -> None:
        if self.feature.mode != "account_scheduler":
            raise ValueError(f"功能 {self.feature.key} 不是账号独立调度模式")
        if not issubclass(self.settings_model, BaseModel):
            raise TypeError("settings_model 必须是 Pydantic BaseModel")
        required_fields = {
            "public_account_identifier",
            "enabled",
            "scheduler",
            "config_mode",
            "private_config",
        }
        missing = required_fields - set(self.account_model.model_fields)
        if self.feature.next_run_field not in self.account_model.model_fields:
            missing.add(str(self.feature.next_run_field))
        if self.feature.forbid_periods:
            if "forbid_periods" not in self.account_model.model_fields:
                missing.add("forbid_periods")
            if self.period_model is None:
                raise ValueError(f"功能 {self.feature.key} 启用了禁止时段但没有 period_model")
        if missing:
            raise ValueError(
                f"账号模型 {self.account_model.__name__} 缺少字段：{', '.join(sorted(missing))}"
            )
        if not self.public_config_attr or not self.public_task_attr:
            raise ValueError("公共配置属性与原任务配置属性不能为空")

    @property
    def section_attr(self) -> str:
        return self.feature.key

    @property
    def api_prefix(self) -> str:
        return self.feature.api_prefix

    @property
    def display_name(self) -> str:
        return self.feature.display_name

    @property
    def overview_kind(self) -> str:
        return self.feature.overview_kind or self.feature.key

    @property
    def settings_group(self) -> str:
        return str(self.feature.settings_group)

    @property
    def settings_path(self) -> str:
        return str(self.feature.settings_path)

    @property
    def next_run_field(self) -> str:
        return str(self.feature.next_run_field)


def create_account_scheduler_router(spec: AccountSchedulerRouterSpec) -> APIRouter:
    router = APIRouter(route_class=ApiLoggingRoute)


    def _section(script_name: str):
        section = getattr(mm.config_cache(script_name).model, spec.section_attr, None)
        if section is None:
            raise HTTPException(status_code=404, detail=f"当前配置没有{spec.display_name}")
        return section


    def _library(script_name: str):
        library = getattr(mm.config_cache(script_name).model, "multi_account_shared_accounts", None)
        if library is None:
            raise HTTPException(status_code=404, detail="当前配置没有公共账号库")
        return library


    def _shared_account_enabled(script_name: str, account: Any) -> bool:
        source = _library(script_name).find(account.public_account_identifier)
        return bool(source is not None and source.enabled)


    def _refresh_outer_scheduler(script_name: str, section) -> None:
        """只使用真正可运行的账号计算外层任务唤醒时间。"""
        next_runs = [
            account.scheduler.next_run
            for account in _accounts(section)
            if (
                account.is_valid()
                and account.enabled
                and account.scheduler.enable
                and _shared_account_enabled(script_name, account)
            )
        ]
        section.scheduler.next_run = (
            min(next_runs).replace(microsecond=0)
            if next_runs
            else datetime.max.replace(microsecond=0)
        )


    def _save(script_name: str, section) -> None:
        _refresh_outer_scheduler(script_name, section)
        mm.config_cache(script_name).save_selected_fields({spec.section_attr: section})


    async def _broadcast_overview(script_name: str) -> None:
        """沿用 OAS WebSocket，通知所有页面重新拉取并按虚拟调度顺序显示。"""
        process = mm.script_process.get(script_name)
        if process is not None:
            await process.broadcast_state({
                "multi_account_overview": {"kind": spec.overview_kind},
            })
            config = mm.config_cache(script_name)
            config.get_next()
            await process.broadcast_state({"schedule": config.get_schedule_data()})


    def _public_account(library, identifier: str) -> SharedPublicAccount:
        account = library.find(identifier)
        if account is None:
            raise HTTPException(status_code=404, detail="公共账号不存在")
        return account


    def _accounts(section) -> list[Any]:
        return [item for item in section.account_list if item.public_account_identifier.strip()]


    def _account(section, index: int) -> Any:
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


    def _default_settings_config() -> dict:
        return {spec.settings_group: spec.settings_model().model_dump()}


    def _account_settings_config(account: Any) -> BaseModel:
        return spec.settings_model.model_validate(account.private_config.get(spec.settings_group, {}))


    def _validated_model_value(model: BaseModel, argument: str, value):
        """将 API 原始值先合并到字典，再交给 Pydantic 转换为字段实际类型。"""
        return model_with_field_overrides(model, {argument: value})


    def _account_scheduler(account: Any) -> Any:
        return account.scheduler


    def _scheduler_next_run(scheduler: Any, *, run_now: bool) -> datetime:
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


    def _require_forbid_periods() -> None:
        if not spec.feature.forbid_periods:
            raise HTTPException(status_code=400, detail="该功能未启用禁止时段")


    def _parse_target_indexes(raw: str) -> set[int]:
        try:
            result = {int(item.strip()) for item in raw.split(",") if item.strip()}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="目标账号序号格式错误") from exc
        if not result:
            raise HTTPException(status_code=400, detail="请至少选择一个目标账号")
        return result


    @router.get(f'/{{script_name}}/{spec.api_prefix}/public-accounts')
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


    @router.get(f'/{{script_name}}/{spec.api_prefix}/accounts')
    async def list_accounts(script_name: str):
        section = _section(script_name)
        now = datetime.now()
        rows = []
        for index, account in enumerate(_accounts(section), start=1):
            scheduler = _account_scheduler(account)
            next_run = scheduler.next_run
            shared_enabled = _shared_account_enabled(script_name, account)
            runnable = scheduler.enable and account.enabled and shared_enabled
            rows.append({
                "index": index,
                "public_account_identifier": account.public_account_identifier,
                "character": account.character,
                "svr": account.svr,
                "enabled": scheduler.enable,
                "account_enabled": account.enabled,
                "shared_account_enabled": shared_enabled,
                "next_run": next_run.isoformat(sep=" ", timespec="seconds"),
                spec.next_run_field: next_run.isoformat(sep=" ", timespec="seconds"),
                "priority": scheduler.priority,
                "schedule_status": "pending" if runnable and next_run <= now else "waiting",
                "forbid_period_count": len(account.forbid_periods),
                "_next_run_value": next_run,
            })
        rows.sort(key=lambda row: _overview_sort_key({**row, "next_run_value": row["_next_run_value"]}, row["index"]))
        for row in rows:
            row.pop("priority", None)
            row.pop("_next_run_value", None)
        return {"accounts": rows}


    @router.post(f'/{{script_name}}/{spec.api_prefix}/accounts')
    async def add_account(script_name: str, public_account_identifier: str):
        section = _section(script_name)
        source = _public_account(_library(script_name), public_account_identifier)
        if any(item.public_account_identifier == source.identifier for item in section.account_list):
            return True
        account = spec.account_model()
        account.sync_public_account(source)
        account.scheduler.enable = True
        setattr(account, spec.next_run_field, account.scheduler.next_run)
        account.private_config = _default_settings_config()
        section.account_list.append(account)
        _save(script_name, section)
        await _broadcast_overview(script_name)
        return True


    @router.delete(f'/{{script_name}}/{spec.api_prefix}/accounts/{{account_index}}')
    async def delete_account(script_name: str, account_index: int):
        section = _section(script_name)
        section.account_list.remove(_account(section, account_index))
        _save(script_name, section)
        await _broadcast_overview(script_name)
        return True


    @router.get(f'/{{script_name}}/{spec.api_prefix}/public-args')
    async def get_public_args(script_name: str):
        section = _section(script_name)
        return {
            # 保留外层 OAS 调度器：控制该功能本身的启用与总优先级；
            # 账号行各自的 Scheduler 决定具体任务时间。
            "scheduler": _serialize_group(section.scheduler),
            spec.public_config_attr: _serialize_group(
                getattr(section, spec.public_config_attr)
            ),
        }


    @router.put(f'/{{script_name}}/{spec.api_prefix}/public-args/{{group}}/{{argument}}/value')
    async def set_public_arg(script_name: str, group: str, argument: str, types: str, value):
        section = _section(script_name)
        allowed = {"scheduler", spec.public_config_attr}
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
        await _broadcast_overview(script_name)
        return True


    @router.get(f'/{{script_name}}/{spec.api_prefix}/accounts/{{account_index}}/scheduler-args')
    async def get_account_scheduler_args(script_name: str, account_index: int):
        return {"scheduler": _serialize_group(_account_scheduler(_account(_section(script_name), account_index)))}


    @router.put(f'/{{script_name}}/{spec.api_prefix}/accounts/{{account_index}}/scheduler-args/{{argument}}/value')
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
            raise HTTPException(status_code=400, detail=f"账号任务调度器无效：{exc}") from exc
        account.scheduler = candidate
        setattr(account, spec.next_run_field, candidate.next_run)
        _save(script_name, section)
        await _broadcast_overview(script_name)
        return True


    @router.put(f'/{{script_name}}/{spec.api_prefix}/accounts/{{account_index}}/account-enable')
    async def set_account_local_enabled(script_name: str, account_index: int, enable: bool):
        """切换任务新内的账号开关，不修改该账号 Scheduler 和禁止运行配置。"""
        section = _section(script_name)
        account = _account(section, account_index)
        account.enabled = enable
        _save(script_name, section)
        await _broadcast_overview(script_name)
        return True


    @router.put(f'/{{script_name}}/{spec.api_prefix}/accounts/{{account_index}}/enable')
    async def set_account_enable(script_name: str, account_index: int, value: str):
        section = _section(script_name)
        account = _account(section, account_index)
        account.scheduler.enable = _convert_argument("boolean", value)
        _save(script_name, section)
        await _broadcast_overview(script_name)
        return True


    @router.put(f'/{{script_name}}/{spec.api_prefix}/accounts/{{account_index}}/quick-schedule')
    async def quick_schedule_account(script_name: str, account_index: int, run_now: bool = True):
        section = _section(script_name)
        account = _account(section, account_index)
        account.scheduler.next_run = _scheduler_next_run(account.scheduler, run_now=run_now)
        setattr(account, spec.next_run_field, account.scheduler.next_run)
        _save(script_name, section)
        await _broadcast_overview(script_name)
        return True


    @router.get(f'/{{script_name}}/{spec.api_prefix}/accounts/{{account_index}}/{spec.settings_path}')
    async def get_account_settings_args(script_name: str, account_index: int):
        account = _account(_section(script_name), account_index)
        use_private = getattr(account.config_mode, "value", account.config_mode) == "private"
        active = (
            _account_settings_config(account)
            if use_private
            else getattr(getattr(mm.config_cache(script_name).model, spec.public_task_attr), spec.settings_group)
        )
        arguments = _serialize_group(active)
        arguments.insert(0, {
            "name": "config_mode",
            "title": "config_mode",
            "description": "config_mode_help",
            "default": "private",
            "value": getattr(account.config_mode, "value", account.config_mode),
            "type": "enum",
            "enumEnum": ["public", "private"],
        })
        return {spec.settings_group: arguments}


    @router.put(f'/{{script_name}}/{spec.api_prefix}/accounts/{{account_index}}/{spec.settings_path}/{{argument}}/value')
    async def set_account_settings_arg(script_name: str, account_index: int, argument: str, types: str, value):
        section = _section(script_name)
        account = _account(section, account_index)
        if convert_to_underscore(argument) == "config_mode":
            if value not in {"public", "private"}:
                raise HTTPException(status_code=400, detail="配置来源无效")
            account.config_mode = value
            _save(script_name, section)
            return True
        if getattr(account.config_mode, "value", account.config_mode) != "private":
            raise HTTPException(status_code=400, detail="当前使用公共配置，请先切换为私有配置")
        candidate = _account_settings_config(account)
        try:
            candidate = _validated_model_value(
                candidate,
                argument,
                _convert_argument(types, value),
            )
        except (ValidationError, ValueError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=f"账号任务配置无效：{exc}") from exc
        account.private_config = {spec.settings_group: candidate.model_dump()}
        _save(script_name, section)
        return True


    @router.put(f'/{{script_name}}/{spec.api_prefix}/accounts/{{account_index}}/{spec.settings_path}/default')
    async def reset_account_settings_args(script_name: str, account_index: int):
        section = _section(script_name)
        _account(section, account_index).private_config = _default_settings_config()
        _save(script_name, section)
        return True


    @router.post(f'/{{script_name}}/{spec.api_prefix}/accounts/{{account_index}}/{spec.settings_path}/copy')
    async def copy_account_settings_args(script_name: str, account_index: int, target_account_indexes: str):
        section = _section(script_name)
        source = _account(section, account_index)
        targets = _parse_target_indexes(target_account_indexes)
        copied = 0
        for target_index in targets:
            if target_index == account_index:
                continue
            target = _account(section, target_index)
            target.config_mode = source.config_mode
            target.private_config = copy.deepcopy(source.private_config)
            copied += 1
        if not copied:
            raise HTTPException(status_code=400, detail="没有可复制的目标账号")
        _save(script_name, section)
        return True


    @router.get(f'/{{script_name}}/{spec.api_prefix}/accounts/{{account_index}}/forbid-periods')
    async def get_forbid_periods(script_name: str, account_index: int):
        _require_forbid_periods()
        account = _account(_section(script_name), account_index)
        return {
            "periods": [
                {"index": index, "start": period.start.isoformat(), "end": period.end.isoformat()}
                for index, period in enumerate(account.forbid_periods, start=1)
            ]
        }


    @router.post(f'/{{script_name}}/{spec.api_prefix}/accounts/{{account_index}}/forbid-periods')
    async def add_forbid_period(script_name: str, account_index: int):
        _require_forbid_periods()
        section = _section(script_name)
        _account(section, account_index).forbid_periods.append(spec.period_model())
        _save(script_name, section)
        return True


    @router.put(f'/{{script_name}}/{spec.api_prefix}/accounts/{{account_index}}/forbid-periods/{{period_index}}')
    async def update_forbid_period(script_name: str, account_index: int, period_index: int, start: str, end: str):
        _require_forbid_periods()
        section = _section(script_name)
        account = _account(section, account_index)
        if period_index < 1 or period_index > len(account.forbid_periods):
            raise HTTPException(status_code=404, detail="禁止时段不存在")
        try:
            account.forbid_periods[period_index - 1] = spec.period_model(
                start=start, end=end
            )
        except (ValidationError, ValueError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=f"禁止时段格式无效：{exc}") from exc
        _save(script_name, section)
        return True


    @router.delete(f'/{{script_name}}/{spec.api_prefix}/accounts/{{account_index}}/forbid-periods/{{period_index}}')
    async def delete_forbid_period(script_name: str, account_index: int, period_index: int):
        _require_forbid_periods()
        section = _section(script_name)
        account = _account(section, account_index)
        if period_index < 1 or period_index > len(account.forbid_periods):
            raise HTTPException(status_code=404, detail="禁止时段不存在")
        account.forbid_periods.pop(period_index - 1)
        _save(script_name, section)
        return True


    @router.put(f'/{{script_name}}/{spec.api_prefix}/accounts/{{account_index}}/forbid-periods/default')
    async def reset_forbid_periods(script_name: str, account_index: int):
        _require_forbid_periods()
        section = _section(script_name)
        _account(section, account_index).forbid_periods = []
        _save(script_name, section)
        return True


    @router.post(f'/{{script_name}}/{spec.api_prefix}/accounts/{{account_index}}/forbid-periods/copy')
    async def copy_forbid_periods(script_name: str, account_index: int, target_account_indexes: str):
        _require_forbid_periods()
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

    return router
