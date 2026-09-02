from datetime import datetime, time
from typing import Any

from pydantic import Field, model_serializer, model_validator

from tasks.Component.MultiAccount.multi_account_config import (
    as_dict,
    dump_model,
    load_indexed_models,
    serialize_indexed_models,
)
from tasks.Component.MultiAccount.shared_public_accounts import SharedPublicAccount
from tasks.Component.config_base import ConfigBase, Time
from tasks.KekkaiUtilize.config import UtilizeConfig, UtilizeScheduler


class MultiAccountKekkaiUtilizeNewConfig(ConfigBase, extra="allow"):
    """多账号多任务蹭卡新的公共运行设置。"""

    check_higher_priority_task: bool = Field(
        default=False,
        title="是否检查更高优先级任务",
        description="发现已到期的更高优先级任务时，先结束当前任务，待高优先级任务完成后继续执行。",
    )


class MultiAccountKekkaiUtilizeNewForbidPeriod(ConfigBase):
    """一个账号自己的禁止蹭卡时间段。开始与结束相同视为无效时段。"""

    start: Time = Field(default=time.fromisoformat("00:00:00"), title="开始时间")
    end: Time = Field(default=time.fromisoformat("00:00:00"), title="结束时间")


class MultiAccountKekkaiUtilizeNewAccount(ConfigBase, extra="allow"):
    """一个公共账号及其独立蹭卡配置、禁止时段和运行时间。"""

    public_account_identifier: str = Field(default="", title="公共账号标识")
    character: str = Field(default="")
    svr: str = Field(default="")
    account: str = Field(default="")
    account_alias: str = Field(default="")
    apple_or_android: bool = Field(default=True)
    # 每个账号都是一个独立的“虚拟 OAS 蹭卡任务”，拥有完整 Scheduler。
    scheduler: UtilizeScheduler = Field(default_factory=UtilizeScheduler)
    # 兼容旧配置与旧客户端；运行时与 scheduler.next_run 始终同步。
    next_utilize_time: datetime = Field(
        default=datetime(2023, 1, 1),
        title="下一次蹭卡时间",
    )
    last_complete_time: datetime = Field(default=datetime(2023, 1, 1))
    # 每个账号始终使用自己的一份配置；初始值来自结界蹭卡默认配置。
    private_config: dict[str, Any] = Field(
        default_factory=lambda: {"utilize_config": UtilizeConfig().model_dump()},
        json_schema_extra={"default": {}},
    )
    # 默认没有禁止蹭卡时段，可在页面中按需添加多条。
    forbid_periods: list[MultiAccountKekkaiUtilizeNewForbidPeriod] = Field(default_factory=list)

    def is_valid(self) -> bool:
        return bool(self.public_account_identifier.strip() and self.character.strip() and self.svr.strip())

    def sync_public_account(self, source: SharedPublicAccount) -> None:
        self.public_account_identifier = source.identifier.strip()
        self.character = source.character
        self.svr = source.svr
        self.account = source.account
        self.account_alias = source.account_alias
        self.apple_or_android = source.apple_or_android

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_configs(cls, value: Any) -> Any:
        """兼容首版新增功能的固定两段禁止时段配置。"""
        if not isinstance(value, dict):
            return value
        data = dict(value)
        # 首版中空配置代表使用公共配置；新版改为每个账号各自使用默认配置。
        if "scheduler" not in data:
            data["scheduler"] = {
                "enable": True,
                "next_run": data.get("next_utilize_time", "2023-01-01 00:00:00"),
            }
        elif isinstance(data.get("scheduler"), dict) and "next_run" not in data["scheduler"]:
            data["scheduler"] = {
                **data["scheduler"],
                "next_run": data.get("next_utilize_time", "2023-01-01 00:00:00"),
            }
        if not data.get("private_config"):
            data["private_config"] = {"utilize_config": UtilizeConfig().model_dump()}
        if "forbid_periods" not in data:
            periods = []
            legacy = data.get("private_forbid_config", {})
            if isinstance(legacy, dict):
                values = legacy.get("private_forbid_config", legacy)
                if isinstance(values, dict):
                    for index in range(1, 3):
                        start = values.get(f"utilize_forbidden_time_start_{index}")
                        end = values.get(f"utilize_forbidden_time_end_{index}")
                        if start and end and start != end:
                            periods.append({"start": start, "end": end})
            data["forbid_periods"] = periods
        data.pop("private_forbid_config", None)
        return data

    @model_validator(mode="after")
    def sync_legacy_next_utilize_time(self):
        self.next_utilize_time = self.scheduler.next_run
        return self


class MultiAccountKekkaiUtilizeNew(ConfigBase, extra="allow"):
    """使用通用公共账号库的独立多账号蹭卡功能。"""

    scheduler: UtilizeScheduler = Field(default_factory=UtilizeScheduler)
    multi_account_kekkai_utilize_new_config: MultiAccountKekkaiUtilizeNewConfig = Field(
        default_factory=MultiAccountKekkaiUtilizeNewConfig,
        title="多账号多任务蹭卡新公共配置",
    )
    account_list: list[MultiAccountKekkaiUtilizeNewAccount] = Field(
        default_factory=list,
        json_schema_extra={"default": []},
    )

    @model_validator(mode="before")
    @classmethod
    def validator_all(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        accounts = load_indexed_models(data, "account_list", MultiAccountKekkaiUtilizeNewAccount)
        data["account_list"] = [account for account in accounts if account.public_account_identifier.strip()]
        data["multi_account_kekkai_utilize_new_config"] = MultiAccountKekkaiUtilizeNewConfig(
            **as_dict(data.get("multi_account_kekkai_utilize_new_config"))
        )
        # 首版的公共蹭卡/禁止时段字段已不参与新版运行，保留 extra 仅用于兼容旧配置文件。
        return data

    @model_serializer()
    def serializer_model(self, value: Any) -> dict[str, Any]:
        data: dict[str, Any] = {}
        for key, current_value in self.__dict__.items():
            if key in {"utilize_config", "forbid_config"}:
                continue
            if isinstance(current_value, list):
                serialize_indexed_models(data, key, current_value)
            else:
                data[key] = dump_model(current_value)
        return data
