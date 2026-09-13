from datetime import datetime, time
from typing import Any

from pydantic import Field, model_serializer, model_validator

from tasks.Component.MultiAccount.multi_account_config import (
    as_dict,
    dump_model,
    load_indexed_models,
    serialize_indexed_models,
)
from tasks.Component.MultiAccount.scheduled_account_config import ScheduledAccountBase
from tasks.Component.config_base import ConfigBase, Time
from tasks.KekkaiActivation.config import ActivationConfig, ActivationScheduler


class MultiAccountKekkaiActivationNewConfig(ConfigBase, extra="allow"):
    """多账号多任务挂卡新的公共运行设置。"""

    check_higher_priority_task: bool = Field(
        default=False,
        title="是否检查更高优先级任务",
        description="发现已到期的更高优先级任务时，先结束当前任务，待高优先级任务完成后继续执行。",
    )


class MultiAccountKekkaiActivationNewForbidPeriod(ConfigBase):
    """一个账号自己的禁止挂卡时间段。开始与结束相同视为无效时段。"""

    start: Time = Field(default=time.fromisoformat("00:00:00"), title="开始时间")
    end: Time = Field(default=time.fromisoformat("00:00:00"), title="结束时间")


class MultiAccountKekkaiActivationNewAccount(ScheduledAccountBase, extra="allow"):
    """一个公共账号及其独立挂卡配置、禁止时段和运行时间。"""

    # 每个账号都是一个独立的“虚拟 OAS 挂卡任务”，拥有完整 Scheduler。
    scheduler: ActivationScheduler = Field(default_factory=ActivationScheduler)
    # 兼容旧配置与旧客户端；运行时与 scheduler.next_run 始终同步。
    next_activation_time: datetime = Field(
        default=datetime(2023, 1, 1),
        title="下一次挂卡时间",
    )
    private_config: dict[str, Any] = Field(
        default_factory=lambda: {"activation_config": ActivationConfig().model_dump()},
        json_schema_extra={"default": {}},
    )
    # 默认没有禁止挂卡时段，可在页面中按需添加多条。
    forbid_periods: list[MultiAccountKekkaiActivationNewForbidPeriod] = Field(default_factory=list)

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
                "next_run": data.get("next_activation_time", "2023-01-01 00:00:00"),
            }
        elif isinstance(data.get("scheduler"), dict) and "next_run" not in data["scheduler"]:
            data["scheduler"] = {
                **data["scheduler"],
                "next_run": data.get("next_activation_time", "2023-01-01 00:00:00"),
            }
        if not data.get("private_config"):
            data["private_config"] = {"activation_config": ActivationConfig().model_dump()}
        if "forbid_periods" not in data:
            periods = []
            legacy = data.get("private_forbid_config", {})
            if isinstance(legacy, dict):
                values = legacy.get("private_forbid_config", legacy)
                if isinstance(values, dict):
                    for index in range(1, 3):
                        start = values.get(f"activation_forbidden_time_start_{index}")
                        end = values.get(f"activation_forbidden_time_end_{index}")
                        if start and end and start != end:
                            periods.append({"start": start, "end": end})
            data["forbid_periods"] = periods
        data.pop("private_forbid_config", None)
        return data

    @model_validator(mode="after")
    def sync_legacy_next_activation_time(self):
        self.next_activation_time = self.scheduler.next_run
        return self


class MultiAccountKekkaiActivationNew(ConfigBase, extra="allow"):
    """使用通用公共账号库的独立多账号挂卡功能。"""

    scheduler: ActivationScheduler = Field(default_factory=ActivationScheduler)
    multi_account_kekkai_activation_new_config: MultiAccountKekkaiActivationNewConfig = Field(
        default_factory=MultiAccountKekkaiActivationNewConfig,
        title="多账号多任务挂卡新公共配置",
    )
    account_list: list[MultiAccountKekkaiActivationNewAccount] = Field(
        default_factory=list,
        json_schema_extra={"default": []},
    )

    @model_validator(mode="before")
    @classmethod
    def validator_all(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        accounts = load_indexed_models(data, "account_list", MultiAccountKekkaiActivationNewAccount)
        data["account_list"] = [account for account in accounts if account.public_account_identifier.strip()]
        data["multi_account_kekkai_activation_new_config"] = MultiAccountKekkaiActivationNewConfig(
            **as_dict(data.get("multi_account_kekkai_activation_new_config"))
        )
        # 首版的公共挂卡/禁止时段字段已不参与新版运行，保留 extra 仅用于兼容旧配置文件。
        return data

    @model_serializer()
    def serializer_model(self, value: Any) -> dict[str, Any]:
        data: dict[str, Any] = {}
        for key, current_value in self.__dict__.items():
            if key in {"activation_config", "forbid_config"}:
                continue
            if isinstance(current_value, list):
                serialize_indexed_models(data, key, current_value)
            else:
                data[key] = dump_model(current_value)
        return data
