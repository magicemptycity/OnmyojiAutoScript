from datetime import datetime
from typing import Any

from pydantic import Field, model_serializer, model_validator

from tasks.Component.MultiAccount.multi_account_config import (
    as_dict,
    dump_model,
    load_indexed_models,
    serialize_indexed_models,
)
from tasks.Component.config_base import ConfigBase, MultiLine
from tasks.Component.config_scheduler import Scheduler
from tasks.Component.MultiAccount.shared_public_accounts import SharedPublicAccount
from tasks.MultiAccountRepeatTimed.task_name_resolver import TaskNameResolver


class MultiAccountRepeatTimedConfig(ConfigBase, extra="allow"):
    """多账号多任务定时的公共运行配置。"""

    check_higher_priority_task: bool = Field(
        default=False,
        title="是否检查更高优先级任务",
        description="发现已到期的更高优先级任务时，先结束当前任务，待高优先级任务完成后继续执行。",
    )
    rerun_incomplete_accounts: bool = Field(default=False, title="未完成账号自动再执行一次")


class MultiAccountRepeatTimedTask(ConfigBase, extra="allow"):
    """一个账号下的任务和私有参数。"""

    task_name: str = Field(default="")
    private_config: dict[str, Any] = Field(default_factory=dict, json_schema_extra={"default": {}})
    # 自动保存的配置型运行记录（例如每日琐事的 done_record），按账号和任务隔离。
    runtime_record: dict[str, Any] = Field(default_factory=dict, json_schema_extra={"default": {}})
    next_run: datetime = Field(default=datetime(2023, 1, 1), title="任务下次运行时间")


class MultiAccountRepeatTimedAccount(ConfigBase, extra="allow"):
    """多账号多任务定时中的运行账号；登录信息由通用公共账号库同步。"""

    public_account_identifier: str = Field(default="", title="公共账号标识")
    character: str = Field(default="")
    svr: str = Field(default="")
    account: str = Field(default="")
    account_alias: str = Field(default="")
    apple_or_android: bool = Field(default=True)
    last_complete_time: datetime = Field(default=datetime(2023, 1, 1), title="上次完整完成时间")
    completed_task_list: MultiLine = Field(
        default="",
        title="已完成任务列表",
        description="记录本轮已成功完成的任务。任务中断后会保留，用于区分已完成与待继续任务。",
    )
    failed_task_list: MultiLine = Field(default="", title="失败任务列表")
    unfinished_task_list: MultiLine = Field(default="", title="未完成任务列表")
    task_list: list[MultiAccountRepeatTimedTask] = Field(default_factory=list, json_schema_extra={"default": []})

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

    @property
    def completed_task_names(self) -> list[str]:
        return self._resolve_task_names(self.completed_task_list)

    @property
    def failed_task_names(self) -> list[str]:
        return self._resolve_task_names(self.failed_task_list)

    @property
    def unfinished_task_names(self) -> list[str]:
        return self._resolve_task_names(self.unfinished_task_list)

    @property
    def task_names(self) -> list[str]:
        return [entry.task_name for entry in self.task_list if entry.task_name]


class MultiAccountRepeatTimed(ConfigBase, extra="allow"):
    """完全独立的多账号多任务定时。"""

    scheduler: Scheduler = Field(default_factory=Scheduler)
    multi_account_repeat_timed_config: MultiAccountRepeatTimedConfig = Field(
        default_factory=MultiAccountRepeatTimedConfig,
        title="多账号多任务定时公共配置",
    )
    account_list: list[MultiAccountRepeatTimedAccount] = Field(default_factory=list, json_schema_extra={"default": []})

    @model_validator(mode="before")
    @classmethod
    def validator_all(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        shared = as_dict(data.get("multi_account_repeat_timed_config"))
        # 账号数量由账号列表自动决定；旧配置字段读取后直接丢弃。
        shared.pop("account_count", None)
        shared.pop("skip_if_logged_today", None)
        accounts = load_indexed_models(data, "account_list", MultiAccountRepeatTimedAccount)
        accounts = [account for account in accounts if account.public_account_identifier.strip()]
        data["multi_account_repeat_timed_config"] = MultiAccountRepeatTimedConfig(**shared)
        data["account_list"] = accounts
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

    def update_account_login_history(self, account: MultiAccountRepeatTimedAccount) -> None:
        account.last_complete_time = datetime.now()
