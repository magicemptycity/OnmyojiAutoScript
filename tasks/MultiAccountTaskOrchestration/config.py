from datetime import datetime, time, timedelta
from typing import Any

from pydantic import Field, model_serializer, model_validator

from tasks.Component.MultiAccount.multi_account_config import (
    as_dict,
    dump_model,
    load_indexed_models,
    serialize_indexed_models,
)
from tasks.Component.config_base import ConfigBase, DateTime, MultiLine, Time, TimeDelta
from tasks.Component.config_scheduler import ScheduleMode, Scheduler
from tasks.Component.MultiAccount.shared_public_accounts import SharedPublicAccount
from tasks.MultiAccountTaskOrchestration.task_name_resolver import TaskNameResolver
from module.config.utils import convert_to_underscore


class MultiAccountRepeatNewConfig(ConfigBase, extra="allow"):
    """多账号多任务新的公共运行配置。"""

    check_higher_priority_task: bool = Field(
        default=False,
        title="是否检查更高优先级任务",
        description="发现已到期的更高优先级任务时，先结束当前任务，待高优先级任务完成后继续执行。",
    )
    rerun_incomplete_accounts: bool = Field(
        default=False,
        title="未完成账号自动再执行一次",
        description="本轮结束后检查今天仍未完整完成的账号，并仅额外再执行一轮；已完整完成的账号会自动跳过。",
    )


class MultiAccountRepeatNewTask(ConfigBase, extra="allow"):
    """账号下的独立单任务；调度、状态和私有参数都只属于该任务。"""

    task_name: str = Field(default="")
    # 仅编排模式使用任务自身 scheduler 私有覆盖；保留 enable 以兼容新普通旧数据。
    enable: bool = Field(default=True)
    private_config: dict[str, Any] = Field(default_factory=dict, json_schema_extra={"default": {}})
    # 自动保存的配置型运行记录（例如每日琐事的 done_record），按账号和任务隔离。
    runtime_record: dict[str, Any] = Field(default_factory=dict, json_schema_extra={"default": {}})
    last_complete_time: datetime = Field(default=datetime(2023, 1, 1), title="上次完整完成时间")
    task_progress_time: datetime = Field(default=datetime(2023, 1, 1), title="任务进度记录时间")
    status: str = Field(default="pending", title="任务执行状态")


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
    # 顺序任务组属于当前账号；编排仅使用任务组中的 task_list。
    fixed_time_batch_list: list["MultiAccountRepeatNewFixedTimeBatch"] = Field(
        default_factory=list,
        json_schema_extra={"default": []},
    )
    # key 为顺序任务组标识，值为该账号在该任务组中的当天进度。
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
        return [entry.task_name for entry in self.task_list if entry.task_name and entry.enable]


class MultiAccountRepeatNewFixedTimeBatchTask(ConfigBase, extra="allow"):
    """顺序任务组中的一个任务，配置和运行记录只属于该账号该任务组。"""

    task_name: str = Field(default="")
    # 停用时保留该任务项及其私有配置，后续重新启用可直接恢复。
    enable: bool = Field(default=True, title="启用顺序任务组任务")
    private_config: dict[str, Any] = Field(default_factory=dict, json_schema_extra={"default": {}})
    runtime_record: dict[str, Any] = Field(default_factory=dict, json_schema_extra={"default": {}})


class MultiAccountRepeatNewFixedTimeBatch(ConfigBase, extra="allow"):
    """账号下的顺序任务组：一个单账号的独立多任务执行单元。"""

    batch_id: str = Field(default="", title="顺序任务组标识")
    # group 为顺序任务组；single 为兼容统一存储的独立单任务。
    item_type: str = Field(default="group", title="编排项类型")
    name: str = Field(default="", title="顺序任务组名称")
    # 直接保存 OAS 原生 Scheduler；任务组不额外维护时间字段。
    scheduler: Scheduler = Field(default_factory=Scheduler, title="调度器设置")
    last_complete_time: datetime = Field(default=datetime(2023, 1, 1), title="上次完整完成时间")
    task_progress_time: datetime = Field(default=datetime(2023, 1, 1), title="任务进度记录时间")
    completed_task_list: MultiLine = Field(default="", title="已完成任务列表")
    failed_task_list: MultiLine = Field(default="", title="失败任务列表")
    unfinished_task_list: MultiLine = Field(default="", title="未完成任务列表")
    task_list: list[MultiAccountRepeatNewFixedTimeBatchTask] = Field(
        default_factory=list,
        title="顺序任务组任务列表",
        json_schema_extra={"default": []},
    )

    @model_validator(mode="before")
    @classmethod
    def validator_tasks(cls, value: Any) -> Any:
        """兼容旧配置，并迁移为由任务组持有的完整原生 Scheduler。"""
        if not isinstance(value, dict):
            return value
        data = dict(value)
        if not data.get("name"):
            data["name"] = "未命名顺序任务组"
        if data.get("item_type") not in {"group", "single"}:
            data["item_type"] = "group"
        scheduler = data.get("scheduler")
        if isinstance(scheduler, Scheduler):
            scheduler = scheduler.model_dump(mode="json")
        elif not isinstance(scheduler, dict):
            legacy_scheduler_keys = {
                "enable", "next_run", "priority", "success_interval", "failure_interval",
                "run_time", "schedule_mode", "interval_days", "weekdays", "float_time",
            }
            if legacy_scheduler_keys.intersection(data):
                mode = str(data.get("schedule_mode", "daily")).strip().lower()
                scheduler = {
                    "enable": data.get("enable", True) is not False,
                    "next_run": data.get("next_run") or "2023-01-01 00:00:00",
                    "priority": data.get("priority", 5),
                    "success_interval": data.get("success_interval", "1 00:00:00"),
                    "failure_interval": data.get("failure_interval", "1 00:00:00"),
                    "server_update": data.get("run_time", "09:00:00"),
                    "schedule_mode": "weekday" if mode == "weekday" else "interval_days",
                    "delay_date": data.get("interval_days", 1),
                    "weekdays": data.get("weekdays") or list(range(1, 8)),
                    "float_time": data.get("float_time", "00:00:00"),
                }
            else:
                scheduler = None
        if scheduler is not None:
            data["scheduler"] = scheduler
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
                "enable": item.get("enable", True) is not False,
                "private_config": private if isinstance(private, dict) else {},
                "runtime_record": item.get("runtime_record", {}) if isinstance(item.get("runtime_record", {}), dict) else {},
            })
        data["task_list"] = entries
        return data

    @property
    def enable(self) -> bool:
        return self.scheduler.enable

    @property
    def task_names(self) -> list[str]:
        return [entry.task_name for entry in self.task_list if entry.task_name and entry.enable]

    @property
    def completed_task_names(self) -> list[str]:
        return MultiAccountRepeatNewAccount._resolve_internal_task_names(self.completed_task_list)

    @property
    def failed_task_names(self) -> list[str]:
        return MultiAccountRepeatNewAccount._resolve_internal_task_names(self.failed_task_list)

    @property
    def unfinished_task_names(self) -> list[str]:
        return MultiAccountRepeatNewAccount._resolve_internal_task_names(self.unfinished_task_list)

    def task_entry(self, task_name: str) -> MultiAccountRepeatNewFixedTimeBatchTask | None:
        task_key = convert_to_underscore(task_name)
        return next((entry for entry in self.task_list if entry.task_name == task_key), None)


class MultiAccountRepeatNewFixedTimeBatchProgress(ConfigBase, extra="allow"):
    """单个账号在某个顺序任务组中的当天进度。"""

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


class MultiAccountTaskOrchestration(ConfigBase, extra="allow"):
    """多账号任务编排的统一配置。"""

    scheduler: Scheduler = Field(default_factory=Scheduler)
    multi_account_repeat_new_config: MultiAccountRepeatNewConfig = Field(
        default_factory=MultiAccountRepeatNewConfig,
        title="多账号任务编排公共配置",
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
        # 旧进度曾保存在账号字典中；迁移到各顺序任务组本身，避免同账号的多个任务组串状态。
        for account in accounts:
            for batch in account.fixed_time_batch_list:
                legacy_progress = account.fixed_time_batch_progress.get(batch.batch_id)
                if legacy_progress is None or batch.task_progress_time.date() != datetime(2023, 1, 1).date():
                    continue
                batch.task_progress_time = legacy_progress.progress_time
                batch.completed_task_list = legacy_progress.completed_task_list
                batch.failed_task_list = legacy_progress.failed_task_list
                batch.unfinished_task_list = legacy_progress.unfinished_task_list
            account.fixed_time_batch_progress = {}
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
