from datetime import datetime, time
from typing import Any

from pydantic import Field, model_serializer, model_validator

from tasks.Component.MultiAccount.account_library import MultiAccountReference
from tasks.Component.MultiAccount.multi_account_config import (
    as_dict,
    dump_model,
    load_indexed_models,
    pad_parallel_models,
    serialize_account_list,
    serialize_indexed_models,
)
from tasks.Component.config_base import ConfigBase, DateTime, Time
from tasks.Component.config_scheduler import Scheduler


class MultiAccountHuntAccount(MultiAccountReference):
    """多账号狩猎战中的账号信息和运行状态。"""

    next_hunt_time: DateTime = Field(
        default=DateTime.fromisoformat("2023-01-01 00:00:00"),
        description="该账号下一次狩猎战运行时间",
    )
    enable_private_config: bool = Field(default=False, description="是否启用私有配置")


class MultiAccountHuntPrivateConfig(ConfigBase):
    """狩猎战公共或账号私有配置。"""

    kirin_time: Time = Field(default=time(19, 0), description="麒麟时间")
    kirin_switch_soul_enable: bool = Field(default=True, description="麒麟是否启用御魂预设切换")
    kirin_switch_team_enable: bool = Field(default=False, description="麒麟是否启用队伍预设切换")
    kirin_preset_public_enable: str = Field(default="-1,-1", description="麒麟预设分组")
    kirin_battle_timeout: int = Field(
        default=-1,
        ge=-1,
        title="麒麟战斗超时时间",
        description="battle_timeout_help",
    )
    netherworld_time: Time = Field(default=time(19, 0), description="阴界之门时间")
    netherworld_switch_soul_enable: bool = Field(default=True, description="阴界之门是否启用御魂预设切换")
    netherworld_switch_team_enable: bool = Field(default=False, description="阴界之门是否启用队伍预设切换")
    netherworld_preset_public_enable: str = Field(default="-1,-1", description="阴界之门预设分组")
    netherworld_battle_timeout: int = Field(
        default=-1,
        ge=-1,
        title="阴界之门战斗超时时间",
        description="battle_timeout_help",
    )


class MultiAccountHuntCommonConfig(MultiAccountHuntPrivateConfig):
    """所有账号共用的狩猎战配置。"""


class MultiAccountHuntCountConfig(ConfigBase):
    account_count: int = Field(default=1, ge=1, le=99, description="账号数量")


class MultiAccountHunt(ConfigBase, extra="allow"):
    """多账号狩猎战的总配置。"""

    scheduler: Scheduler = Field(default_factory=Scheduler)
    multi_account_hunt_config: MultiAccountHuntCountConfig = Field(
        default_factory=MultiAccountHuntCountConfig,
    )
    common_config: MultiAccountHuntCommonConfig = Field(default_factory=MultiAccountHuntCommonConfig)
    account_list: list[MultiAccountHuntAccount] = Field(default_factory=list)
    private_config: list[MultiAccountHuntPrivateConfig] = Field(default_factory=list)

    def update_account_next_hunt_time(
        self,
        account: MultiAccountHuntAccount,
        next_time: datetime,
    ) -> None:
        for info in self.account_list:
            if info.character == account.character and info.svr == account.svr:
                info.next_hunt_time = next_time
                break

    @model_validator(mode="before")
    @classmethod
    def validator_all(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value

        data = dict(value)
        data.setdefault("account_list", [])
        data.setdefault("private_config", [])
        raw_count = data.get("multi_account_hunt_config")
        count_config = as_dict(raw_count)
        count_config.setdefault("account_count", data.get("account_count", 1))
        count_model = MultiAccountHuntCountConfig(**count_config)
        data["multi_account_hunt_config"] = count_model
        data.pop("account_count", None)

        accounts = load_indexed_models(data, "account_list", MultiAccountHuntAccount)
        private_configs = load_indexed_models(
            data,
            "private_config",
            MultiAccountHuntPrivateConfig,
        )
        pad_parallel_models(
            {"account_list": accounts, "private_config": private_configs},
            count_model.account_count,
            {
                "account_list": MultiAccountHuntAccount,
                "private_config": MultiAccountHuntPrivateConfig,
            },
        )
        for index, account in enumerate(accounts):
            if not account.enable_private_config:
                private_configs[index] = MultiAccountHuntPrivateConfig()

        data["account_list"] = accounts
        data["private_config"] = private_configs
        return data

    @model_serializer()
    def serializer_model(self, value: Any) -> dict[str, Any]:
        data: dict[str, Any] = {}
        for key, current_value in self.__dict__.items():
            if key == "account_list":
                serialize_account_list(
                    data,
                    key,
                    current_value,
                    {
                        "private_config": (
                            self.private_config,
                            "enable_private_config",
                            MultiAccountHuntPrivateConfig,
                        ),
                    },
                )
                continue
            if key == "private_config":
                continue
            if isinstance(current_value, list):
                serialize_indexed_models(data, key, current_value)
            else:
                data[key] = dump_model(current_value)
        return data
