import re
from datetime import datetime
from typing import Any

from pydantic import Field, model_serializer, model_validator

from tasks.Component.MultiAccount.multi_account_config import (
    as_dict,
    dump_model,
    load_indexed_models,
    pad_parallel_models,
    serialize_indexed_models,
)
from tasks.Component.MultiAccount.account_library import MultiAccountReference
from tasks.Component.config_base import ConfigBase, MultiLine
from tasks.Component.config_scheduler import Scheduler
from tasks.MultiAccountRepeat.task_name_resolver import TaskNameResolver


class MultiAccountRepeatConfig(ConfigBase, extra="allow"):
    """多账号多任务的公共调度配置。"""

    check_higher_priority_task: bool = Field(
        default=False,
        title="是否检查更高优先级任务",
        description="发现已到期的更高优先级任务时，先结束当前多账号任务，待高优先级任务完成后继续执行。",
    )
    account_count: int = Field(
        default=1,
        ge=1,
        description="账号数量，决定下面会生成几组账号配置",
    )
    skip_if_logged_today: bool = Field(
        default=True,
        description="如果上次登录时间为今天，则跳过该账号的登录与任务执行",
    )
    rerun_incomplete_accounts: bool = Field(
        default=False,
        title="未完成账号自动再执行一次",
        description="本轮结束后按上次登录时间检查所有目标账号；若仍有账号今天未完成任务，整个多账号多任务再执行一次。需同时开启“今日已执行则跳过”。",
    )


class MultiAccountRepeatTask(ConfigBase, extra="allow"):
    """一个账号下配置的任务及其私有参数覆盖。"""

    task_name: str = Field(default="", description="内部任务名称")
    private_config: dict[str, Any] = Field(
        default_factory=dict,
        description="该账号对本任务的私有参数覆盖；为空时使用公共配置",
    )


class MultiAccountRepeatAccount(MultiAccountReference):
    """多账号多任务中的账号和任务列表。"""

    task_list: list[MultiAccountRepeatTask] = Field(
        default_factory=list,
        title="任务列表",
        description="当前账号要执行的任务及其私有配置",
    )
    repeat_task_list: MultiLine = Field(
        default="",
        description=(
            "需要重复执行的任务名称，多个任务请换行填写。例如：悬赏封印\n契灵之境"
        ),
    )

    failed_task_list: MultiLine = Field(
        default="",
        title="失败任务列表",
        description="自动记录连续重试仍失败的任务，下次仅重试这些任务。可手动清空。",
    )
    unfinished_task_list: MultiLine = Field(
        default="",
        title="未完成任务列表",
        description="自动记录本轮尚未完成的任务。每项任务开始前都会保存；脚本被手动停止后，下次从这里继续执行。可手动清空。",
    )

    @model_validator(mode="before")
    @classmethod
    def normalize_repeat_task_list(cls, value: Any) -> Any:
        """兼容旧配置中被包装成字典的任务列表。"""
        if not isinstance(value, dict):
            return value
        data = dict(value)
        repeat_task_list = data.get("repeat_task_list")
        if isinstance(repeat_task_list, dict):
            data["repeat_task_list"] = repeat_task_list.get(
                "repeat_task_list",
                "",
            )
        return data

    @staticmethod
    def _resolve_task_names(task_list: str) -> list[str]:
        """将配置中的任务列表转换为内部任务名。"""
        names = []
        for line in task_list.split("\n"):
            raw = line.strip()
            if not raw:
                continue
            resolved = TaskNameResolver.resolve(raw)
            names.append(resolved or raw)
        return names

    @property
    def failed_task_names(self) -> list[str]:
        """将失败任务列表转换为内部任务名。"""
        return self._resolve_task_names(self.failed_task_list)

    @property
    def unfinished_task_names(self) -> list[str]:
        """将上次中断时未完成的任务列表转换为内部任务名。"""
        return self._resolve_task_names(self.unfinished_task_list)

    @property
    def task_entries(self) -> list[MultiAccountRepeatTask]:
        """返回结构化任务列表；旧配置没有新列表时回退到旧文本列表。"""
        if self.task_list:
            return self.task_list
        return [
            MultiAccountRepeatTask(task_name=name)
            for name in self._resolve_task_names(self.repeat_task_list)
        ]

    @property
    def repeat_task_names(self) -> list[str]:
        """把账号配置中的任务名称转换为内部任务名。"""
        return [entry.task_name for entry in self.task_entries if entry.task_name]


class MultiAccountRepeat(ConfigBase, extra="allow"):
    """多账号多任务的总配置。"""

    scheduler: Scheduler = Field(default_factory=Scheduler)
    multi_account_repeat_config: MultiAccountRepeatConfig = Field(
        default_factory=MultiAccountRepeatConfig,
        title="公共多账号多任务配置",
        description="所有账号共用的多账号多任务调度参数",
    )
    account_list: list[MultiAccountRepeatAccount] = Field(default_factory=list)

    def update_account_login_history(self, account: MultiAccountRepeatAccount) -> None:
        """更新账号最近一次完成时间。"""
        for info in self.account_list:
            if info.character == account.character and info.svr == account.svr:
                info.last_complete_time = datetime.now()
                break

    @model_validator(mode="before")
    @classmethod
    def validator_all(cls, value: Any) -> Any:
        """兼容旧配置，并还原 OASX 的扁平账号配置组。"""
        if not isinstance(value, dict):
            return value

        data = dict(value)
        data.setdefault("account_list", [])

        raw_shared = data.get("multi_account_repeat_config")
        shared_config = as_dict(raw_shared)
        if not shared_config:
            shared_config = as_dict(
                data.get("config")
                or data.get("shared_config")
            )

        if "account_count" not in shared_config:
            shared_config["account_count"] = data.get(
                "account_count",
                1,
            )
        try:
            shared_config["account_count"] = int(shared_config["account_count"])
        except (TypeError, ValueError):
            shared_config["account_count"] = 1

        public_model = MultiAccountRepeatConfig(**shared_config)
        data["multi_account_repeat_config"] = public_model
        for alias in ("config", "shared_config", "account_count"):
            data.pop(alias, None)

        accounts = load_indexed_models(
            data,
            "account_list",
            MultiAccountRepeatAccount,
        )
        # 只压缩多账号多任务自身的不完整本地账号，不处理公共账号库。
        # 角色名或服务器名任一为空且公共账号标识为空时，移除该账号行。
        # 公共账号标识保存在账号对象内部，账号对象整体移动后，标识值不会改变。
        accounts = [
            account
            for account in accounts
            if not (
                (
                    not str(account.character or "").strip()
                    or not str(account.svr or "").strip()
                )
                and not str(account.shared_account_identifier or "").strip()
            )
        ]
        pad_parallel_models(
            {"account_list": accounts},
            public_model.account_count,
            {"account_list": MultiAccountRepeatAccount},
        )
        data["account_list"] = accounts
        return data

    @model_serializer()
    def serializer_model(self, value: Any) -> dict[str, Any]:
        """把账号列表序列化成前端可以逐组显示的扁平字段。"""
        data: dict[str, Any] = {}
        for key, current_value in self.__dict__.items():
            if isinstance(current_value, list):
                serialize_indexed_models(data, key, current_value)
            else:
                data[key] = dump_model(current_value)
        return data
