from datetime import datetime, time
from typing import Any

from pydantic import Field, model_serializer, model_validator

from tasks.Component.MultiAccount.multi_account_config import (
    as_dict,
    dump_model,
    load_indexed_models,
    serialize_indexed_models,
)
from tasks.Component.config_base import ConfigBase, MultiLine, Time
from tasks.Component.config_scheduler import Scheduler
from tasks.Component.MultiAccount.shared_public_accounts import SharedPublicAccount
from tasks.MultiAccountRepeatNew.task_name_resolver import TaskNameResolver
from module.config.utils import convert_to_underscore


class MultiAccountRepeatNewConfig(ConfigBase, extra="allow"):
    """多账号多任务新的公共运行配置。"""

    check_higher_priority_task: bool = Field(
        default=False,
        title="是否检查更高优先级任务",
        description="发现已到期的更高优先级任务时，先结束当前任务，待高优先级任务完成后继续执行。",
    )
    rerun_incomplete_accounts: bool = Field(default=False, title="未完成账号自动再执行一次")


class MultiAccountRepeatNewTask(ConfigBase, extra="allow"):
    """一个账号下的任务和私有参数。"""

    task_name: str = Field(default="")
    private_config: dict[str, Any] = Field(default_factory=dict, json_schema_extra={"default": {}})
    # 自动保存的配置型运行记录（例如每日琐事的 done_record），按账号和任务隔离。
    runtime_record: dict[str, Any] = Field(default_factory=dict, json_schema_extra={"default": {}})


class MultiAccountRepeatNewAccount(ConfigBase, extra="allow"):
    """多账号多任务新中的运行账号；登录信息由通用公共账号库同步。"""

    public_account_identifier: str = Field(default="", title="公共账号标识")
    character: str = Field(default="")
    svr: str = Field(default="")
    account: str = Field(default="")
    account_alias: str = Field(default="")
    apple_or_android: bool = Field(default=True)
    last_complete_time: datetime = Field(default=datetime(2023, 1, 1), title="上次完整完成时间")
    task_progress_time: datetime = Field(default=datetime(2023, 1, 1), title="任务进度记录时间")
    completed_task_list: MultiLine = Field(
        default="",
        title="已完成任务列表",
        description="记录本轮已成功完成的任务。任务中断后会保留，用于区分已完成与待继续任务。",
    )
    failed_task_list: MultiLine = Field(default="", title="失败任务列表")
    unfinished_task_list: MultiLine = Field(default="", title="未完成任务列表")
    task_list: list[MultiAccountRepeatNewTask] = Field(default_factory=list, json_schema_extra={"default": []})
    # 固定时间批次属于当前账号，任务不依赖 task_list。
    fixed_time_batch_list: list["MultiAccountRepeatNewFixedTimeBatch"] = Field(
        default_factory=list,
        json_schema_extra={"default": []},
    )
    # key 为固定时间批次标识，值为该账号在该批次中的当天进度。
    fixed_time_batch_progress: dict[str, "MultiAccountRepeatNewFixedTimeBatchProgress"] = Field(
        default_factory=dict, json_schema_extra={"default": {}}
    )

    def is_valid(self) -> bool:
        return bool(self.public_account_identifier.strip() and self.character.strip() and self.svr.strip())

    def sync_public_account(self, source: SharedPublicAccount) -> None:
        self.public_account_identifier = source.identifier.strip()
        self.character = source.character
        self.svr = source.svr
        self.account = source.account
        self.account_alias = source.account_alias
        self.apple_or_android = source.apple_or_android

    @staticmethod
    def _resolve_task_names(task_list: str) -> list[str]:
        names: list[str] = []
        for line in task_list.split("\n"):
            raw = line.strip()
            if raw:
                names.append(TaskNameResolver.resolve(raw) or raw)
        return names

    @staticmethod
    def _resolve_internal_task_names(task_list: str) -> list[str]:
        """统一转换为配置和动态加载使用的下划线内部任务标识。"""
        return [
            convert_to_underscore(name)
            for name in MultiAccountRepeatNewAccount._resolve_task_names(task_list)
        ]

    @property
    def completed_task_names(self) -> list[str]:
        return self._resolve_internal_task_names(self.completed_task_list)

    @property
    def failed_task_names(self) -> list[str]:
        return self._resolve_internal_task_names(self.failed_task_list)

    @property
    def unfinished_task_names(self) -> list[str]:
        return self._resolve_internal_task_names(self.unfinished_task_list)

    @property
    def task_names(self) -> list[str]:
        return [entry.task_name for entry in self.task_list if entry.task_name]


class MultiAccountRepeatNewFixedTimeBatchTask(ConfigBase, extra="allow"):
    """固定时间批次中的一个任务，配置和运行记录只属于该账号该批次。"""

    task_name: str = Field(default="")
    private_config: dict[str, Any] = Field(default_factory=dict, json_schema_extra={"default": {}})
    runtime_record: dict[str, Any] = Field(default_factory=dict, json_schema_extra={"default": {}})


class MultiAccountRepeatNewFixedTimeBatch(ConfigBase, extra="allow"):
    """固定时间批次：到达指定时间后，仅为所属账号运行所选任务。"""

    batch_id: str = Field(default="", title="批次标识")
    enable: bool = Field(default=True, title="启用固定时间批次")
    run_time: Time = Field(default=time(9, 0), title="运行时间")
    # daily：每天；interval：每隔指定天数；weekday：指定星期几。
    schedule_mode: str = Field(default="daily", title="运行周期")
    interval_days: int = Field(default=1, ge=1, le=365, title="间隔天数")
    # 采用 ISO 星期序号：1 为周一，7 为周日。
    weekdays: list[int] = Field(default_factory=list, title="指定星期")
    # 仅在整个批次成功完成后更新，用于间隔天数的起算。
    last_run_time: datetime = Field(default=datetime(2023, 1, 1), title="上次成功运行时间")
    task_list: list[MultiAccountRepeatNewFixedTimeBatchTask] = Field(
        default_factory=list,
        title="批次任务列表",
        json_schema_extra={"default": []},
    )

    @model_validator(mode="before")
    @classmethod
    def validator_tasks(cls, value: Any) -> Any:
        """兼容旧版多行任务列表和批次配置字典，迁移为独立任务项。"""
        if not isinstance(value, dict):
            return value
        data = dict(value)
        # 旧批次默认按每天运行；非法周期配置回退为每天，避免影响已有任务。
        mode = str(data.get("schedule_mode", "daily")).strip().lower()
        data["schedule_mode"] = mode if mode in {"daily", "interval", "weekday"} else "daily"
        try:
            data["interval_days"] = max(1, min(365, int(data.get("interval_days", 1))))
        except (TypeError, ValueError):
            data["interval_days"] = 1
        raw_weekdays = data.get("weekdays", [])
        if not isinstance(raw_weekdays, list):
            raw_weekdays = []
        data["weekdays"] = sorted({int(day) for day in raw_weekdays if str(day).isdigit() and 1 <= int(day) <= 7})
        raw_tasks = data.get("task_list", [])
        legacy_private = data.pop("private_config", {})
        if isinstance(raw_tasks, str):
            raw_tasks = [line.strip() for line in raw_tasks.split("\n") if line.strip()]
        if not isinstance(raw_tasks, list):
            raw_tasks = []

        entries: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw_task in raw_tasks:
            item = as_dict(raw_task) if not isinstance(raw_task, str) else {"task_name": raw_task}
            task_key = convert_to_underscore(TaskNameResolver.resolve(str(item.get("task_name", ""))) or str(item.get("task_name", "")))
            if not task_key or task_key in seen:
                continue
            seen.add(task_key)
            private = item.get("private_config", {})
            if not private and isinstance(legacy_private, dict):
                for name, config in legacy_private.items():
                    if convert_to_underscore(str(name)) == task_key and isinstance(config, dict):
                        private = config
                        break
            entries.append({
                "task_name": task_key,
                "private_config": private if isinstance(private, dict) else {},
                "runtime_record": item.get("runtime_record", {}) if isinstance(item.get("runtime_record", {}), dict) else {},
            })
        data["task_list"] = entries
        return data

    @property
    def task_names(self) -> list[str]:
        return [entry.task_name for entry in self.task_list if entry.task_name]

    def task_entry(self, task_name: str) -> MultiAccountRepeatNewFixedTimeBatchTask | None:
        task_key = convert_to_underscore(task_name)
        return next((entry for entry in self.task_list if entry.task_name == task_key), None)


class MultiAccountRepeatNewFixedTimeBatchProgress(ConfigBase, extra="allow"):
    """单个账号在某个固定时间批次中的当天进度。"""

    progress_time: datetime = Field(default=datetime(2023, 1, 1))
    completed_task_list: MultiLine = Field(default="")
    failed_task_list: MultiLine = Field(default="")
    unfinished_task_list: MultiLine = Field(default="")

    @property
    def completed_task_names(self) -> list[str]:
        return MultiAccountRepeatNewAccount._resolve_internal_task_names(self.completed_task_list)

    @property
    def failed_task_names(self) -> list[str]:
        return MultiAccountRepeatNewAccount._resolve_internal_task_names(self.failed_task_list)

    @property
    def unfinished_task_names(self) -> list[str]:
        return MultiAccountRepeatNewAccount._resolve_internal_task_names(self.unfinished_task_list)


class MultiAccountRepeatNew(ConfigBase, extra="allow"):
    """完全独立的多账号多任务新。"""

    scheduler: Scheduler = Field(default_factory=Scheduler)
    multi_account_repeat_new_config: MultiAccountRepeatNewConfig = Field(
        default_factory=MultiAccountRepeatNewConfig,
        title="多账号多任务新公共配置",
    )
    account_list: list[MultiAccountRepeatNewAccount] = Field(default_factory=list, json_schema_extra={"default": []})
    # 仅用于读取旧版全局批次；加载时会迁移到各账号的 fixed_time_batch_list。
    fixed_time_batch_list: list[MultiAccountRepeatNewFixedTimeBatch] = Field(
        default_factory=list, json_schema_extra={"default": []}
    )

    @model_validator(mode="before")
    @classmethod
    def validator_all(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        shared = as_dict(data.get("multi_account_repeat_new_config"))
        # 账号数量由账号列表自动决定；旧配置字段读取后直接丢弃。
        shared.pop("account_count", None)
        shared.pop("skip_if_logged_today", None)
        accounts = load_indexed_models(data, "account_list", MultiAccountRepeatNewAccount)
        accounts = [account for account in accounts if account.public_account_identifier.strip()]
        legacy_batches = load_indexed_models(data, "fixed_time_batch_list", MultiAccountRepeatNewFixedTimeBatch)
        for index, batch in enumerate(legacy_batches, start=1):
            if not batch.batch_id.strip():
                # 兼容早期手动写入的批次配置；新批次由接口生成 UUID。
                batch.batch_id = f"legacy_batch_{index}"
        # 旧版批次曾属于整个功能。为保留既有运行效果，将旧批次复制给尚未设置账号批次的账号。
        if legacy_batches:
            for account in accounts:
                if not account.fixed_time_batch_list:
                    account.fixed_time_batch_list = [batch.model_copy(deep=True) for batch in legacy_batches]
        data["multi_account_repeat_new_config"] = MultiAccountRepeatNewConfig(**shared)
        data["account_list"] = accounts
        # 已迁移后不再使用全局批次；避免下一次加载重复复制。
        data["fixed_time_batch_list"] = []
        return data

    @model_serializer()
    def serializer_model(self, value: Any) -> dict[str, Any]:
        data: dict[str, Any] = {}
        for key, current_value in self.__dict__.items():
            if isinstance(current_value, list):
                serialize_indexed_models(data, key, current_value)
            else:
                data[key] = dump_model(current_value)
        return data

    def update_account_login_history(self, account: MultiAccountRepeatNewAccount) -> None:
        account.last_complete_time = datetime.now()
