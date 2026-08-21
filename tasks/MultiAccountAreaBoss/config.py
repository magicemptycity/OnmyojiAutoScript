from datetime import datetime
from typing import Any

from pydantic import Field, model_serializer, model_validator

from tasks.AreaBoss.config_boss import AreaBossFloor
from tasks.Component.MultiAccount.multi_account_config import (
    as_dict,
    dump_model,
    load_indexed_models,
    pad_parallel_models,
    serialize_account_list,
    serialize_indexed_models,
)
from tasks.Component.MultiAccount.account_library import MultiAccountReference
from tasks.Component.config_base import ConfigBase
from tasks.Component.config_scheduler import Scheduler


class MultiAccountAreaBossAccount(MultiAccountReference):
    """多账号地域鬼王中的账号信息。"""

    enable_private_config: bool = Field(
        default=False,
        title="是否启用私有配置",
        description="是否启用私有地域鬼王配置",
    )


class MultiAccountAreaBossPrivateConfig(ConfigBase):
    """地域鬼王的账号级配置。"""

    boss_number: int = Field(
        default=3,
        ge=1,
        le=3,
        description=(
            "默认为3，可选[1-3]。设置为3时默认拥有全部挑战资格，"
            "会挑战热门的前三个；如果不是，请将可以挑战的鬼王进行收藏"
        ),
    )
    boss_reward: bool = Field(default=False, description="boss_reward_help")
    reward_floor: AreaBossFloor = Field(default=AreaBossFloor.ONE, description="reward_floor_help")
    use_collect: bool = Field(default=False, description="use_collect_help")
    Attack_60: bool = Field(default=False, description="没有开启极是否拉到60级进行攻打")
    lock_team_enable: bool = Field(default=False, description="lock_team_enable_help")
    switch_team_enable: bool = Field(default=False, description="switch_team_enable_help")
    switch_soul_enable: bool = Field(default=False, description="switch_soul_enable_help")
    preset_public_enable: str = Field(default="-1,-1", description="preset_public_enable_help")


class MultiAccountAreaBossCommonConfig(MultiAccountAreaBossPrivateConfig):
    """地域鬼王的公共配置。"""


class MultiAccountAreaBossConfig(ConfigBase, extra="allow"):
    """多账号地域鬼王的调度配置。"""

    account_count: int = Field(
        default=1,
        ge=1,
        description="账号数量，决定下面生成几组账号配置",
    )


class MultiAccountAreaBoss(ConfigBase, extra="allow"):
    """多账号地域鬼王的总配置。"""

    scheduler: Scheduler = Field(default_factory=Scheduler)
    multi_account_area_boss_config: MultiAccountAreaBossConfig = Field(
        default_factory=MultiAccountAreaBossConfig,
    )
    common_config: MultiAccountAreaBossCommonConfig = Field(
        default_factory=MultiAccountAreaBossCommonConfig,
    )
    account_list: list[MultiAccountAreaBossAccount] = Field(default_factory=list)
    private_config: list[MultiAccountAreaBossPrivateConfig] = Field(default_factory=list)

    def update_account_login_history(self, account: MultiAccountAreaBossAccount):
        """更新账号最近一次完成时间。"""
        for info in self.account_list:
            if info.character != account.character or info.svr != account.svr:
                continue
            info.last_complete_time = datetime.now()
            break

    @model_validator(mode="before")
    @classmethod
    def validator_all(cls, value: Any) -> Any:
        """统一还原列表配置，并读取 OASX 返回的扁平字段。"""
        if not isinstance(value, dict):
            return value

        data = dict(value)
        data.setdefault("account_list", [])
        data.setdefault("private_config", [])

        raw_count = data.get("multi_account_area_boss_config")
        count_config = as_dict(raw_count)
        if "account_count" not in count_config:
            count_config["account_count"] = data.get("account_count", 1)
        count_model = (
            raw_count
            if isinstance(raw_count, MultiAccountAreaBossConfig)
            else MultiAccountAreaBossConfig(**count_config)
        )
        data["multi_account_area_boss_config"] = count_model
        data.pop("account_count", None)

        accounts = load_indexed_models(
            data,
            "account_list",
            MultiAccountAreaBossAccount,
        )
        private_configs = load_indexed_models(
            data,
            "private_config",
            MultiAccountAreaBossPrivateConfig,
        )

        pad_parallel_models(
            {
                "account_list": accounts,
                "private_config": private_configs,
            },
            count_model.account_count,
            {
                "account_list": MultiAccountAreaBossAccount,
                "private_config": MultiAccountAreaBossPrivateConfig,
            },
        )

        # 未启用私有配置的账号始终使用公共配置，避免旧配置残留影响运行。
        for index, account in enumerate(accounts):
            if not account.enable_private_config:
                private_configs[index] = MultiAccountAreaBossPrivateConfig()

        data["account_list"] = accounts
        data["private_config"] = private_configs
        return data

    @model_serializer()
    def serializer_model(self, value: Any) -> dict[str, Any]:
        """把内部列表序列化成前端可识别的账号配置组。"""
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
                            MultiAccountAreaBossPrivateConfig,
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
