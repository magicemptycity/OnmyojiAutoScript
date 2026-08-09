from datetime import datetime, time
from typing import Any

from pydantic import Field, model_serializer, model_validator

from tasks.Component.MultiAccount.multi_account_config import (
    as_dict,
    dump_model,
    load_indexed_models,
    pad_parallel_models,
    serialize_account_list,
    serialize_indexed_models,
)
from tasks.Component.SwitchAccount.switch_account_config import AccountInfo
from tasks.Component.config_base import ConfigBase, DateTime, Time
from tasks.Component.config_scheduler import Scheduler
from tasks.KekkaiUtilize.config import SelectFriendList, UtilizeRule
from tasks.Utils.config_enum import ShikigamiClass


class MultiAccountKekkaiUtilizeAccount(AccountInfo):
    """多账号蹭卡中的账号信息和账号级调度状态。"""

    next_utilize_time: DateTime = Field(
        default=DateTime.fromisoformat("2023-01-01 00:00:00"),
        description="每个账号下一次蹭卡时间",
    )
    enable_private_utilize_config: bool = Field(
        default=False,
        description="是否启用私有结界蹭卡配置",
    )
    enable_private_forbid_time: bool = Field(
        default=False,
        description="是否启用私有禁止蹭卡时段",
    )


class MultiAccountKekkaiUtilizeConfig(ConfigBase, extra="allow"):
    """多账号蹭卡的公共结界配置。"""

    utilize_rule: UtilizeRule = Field(default=UtilizeRule.DEFAULT, description="utilize_rule_help")
    select_friend_list: SelectFriendList = Field(
        default=SelectFriendList.SAME_SERVER,
        description="select_friend_list_help",
    )
    auto_fill: bool = Field(default=False, description="auto_fill_help")
    shikigami_class: ShikigamiClass = Field(
        default=ShikigamiClass.N,
        description="shikigami_class_help",
    )
    shikigami_order: int = Field(default=4, description="shikigami_order_help")
    harvest_guild_max_times: int = Field(default=3, description="harvest_guild_max_times_help")
    utilize_harvest: bool = Field(default=True, description="utilize_harvest_help")
    utilize_enable: bool = Field(default=True, description="utilize_enable_help")
    box_ap_enable: bool = Field(default=True)
    box_exp_enable: bool = Field(default=True)
    box_exp_waste: bool = Field(default=True, description="box_exp_waste_help")


class MultiAccountKekkaiUtilizeCountConfig(ConfigBase, extra="allow"):
    """多账号蹭卡的账号数量配置。"""

    account_count: int = Field(
        default=1,
        ge=1,
        title="账号数量",
        description="账号数量，决定下面生成几组账号配置",
    )


class MultiAccountKekkaiUtilizeForbidConfig(ConfigBase, extra="allow"):
    """多账号蹭卡的公共禁止时段配置。"""

    public_forbid_time_enable: bool = Field(
        default=False,
        title="启用公共禁止蹭卡时段",
        description="是否启用公共禁止蹭卡时间段",
    )
    public_forbid_time_start: Time = Field(
        default=time.fromisoformat("00:00:00"),
        title="禁止蹭卡开始时间",
        description="公共禁止蹭卡时间段开始时间",
    )
    public_forbid_time_end: Time = Field(
        default=time.fromisoformat("00:00:00"),
        title="禁止蹭卡结束时间",
        description="公共禁止蹭卡时间段结束时间",
    )


class MultiAccountKekkaiUtilizePrivateUtilizeConfig(ConfigBase):
    """账号私有结界蹭卡配置。"""

    utilize_rule: UtilizeRule = Field(default=UtilizeRule.DEFAULT, description="utilize_rule_help")
    select_friend_list: SelectFriendList = Field(
        default=SelectFriendList.SAME_SERVER,
        description="select_friend_list_help",
    )
    auto_fill: bool = Field(default=False, description="auto_fill_help")
    shikigami_class: ShikigamiClass = Field(
        default=ShikigamiClass.N,
        description="shikigami_class_help",
    )
    shikigami_order: int = Field(default=4, description="shikigami_order_help")
    harvest_guild_max_times: int = Field(default=3, description="harvest_guild_max_times_help")
    utilize_harvest: bool = Field(default=True, description="utilize_harvest_help")
    utilize_enable: bool = Field(default=True, description="utilize_enable_help")
    box_ap_enable: bool = Field(default=True)
    box_exp_enable: bool = Field(default=True)
    box_exp_waste: bool = Field(default=True, description="box_exp_waste_help")


class MultiAccountKekkaiUtilizePrivateForbidConfig(ConfigBase):
    """账号私有禁止蹭卡时段配置。"""

    utilize_forbidden_time_enable: bool = Field(
        default=False,
        description="是否启用该账号的禁止蹭卡时间段",
    )
    utilize_forbidden_time_start: Time = Field(
        default=time.fromisoformat("00:00:00"),
        description="账号禁止蹭卡时间段开始时间",
    )
    utilize_forbidden_time_end: Time = Field(
        default=time.fromisoformat("00:00:00"),
        description="账号禁止蹭卡时间段结束时间",
    )


class MultiAccountKekkaiUtilize(ConfigBase, extra="allow"):
    """多账号蹭卡的总配置。"""

    scheduler: Scheduler = Field(default_factory=Scheduler)
    multi_account_kekkai_count_config: MultiAccountKekkaiUtilizeCountConfig = Field(
        default_factory=MultiAccountKekkaiUtilizeCountConfig,
        title="账号数量",
        description="账号数量，决定下面生成几组账号配置",
    )
    multi_account_kekkai_forbid_config: MultiAccountKekkaiUtilizeForbidConfig = Field(
        default_factory=MultiAccountKekkaiUtilizeForbidConfig,
        title="公共禁止时段",
        description="公共禁止蹭卡时间段配置",
    )
    multi_account_kekkai_utilize_config: MultiAccountKekkaiUtilizeConfig = Field(
        default_factory=MultiAccountKekkaiUtilizeConfig,
        title="公共结界蹭卡配置",
        description="多账号蹭卡的共享结界蹭卡参数",
    )
    account_list: list[MultiAccountKekkaiUtilizeAccount] = Field(default_factory=list)
    private_utilize_config: list[MultiAccountKekkaiUtilizePrivateUtilizeConfig] = Field(
        default_factory=list,
    )
    private_forbid_config: list[MultiAccountKekkaiUtilizePrivateForbidConfig] = Field(
        default_factory=list,
    )

    def update_account_next_utilize_time(
        self,
        account: MultiAccountKekkaiUtilizeAccount,
        next_time: datetime,
    ):
        """更新指定账号的下一次蹭卡时间。"""
        for info in self.account_list:
            if info.character == account.character and info.svr == account.svr:
                info.next_utilize_time = next_time
                break

    def update_account_login_history(self, account: MultiAccountKekkaiUtilizeAccount):
        """更新账号最近一次完成时间。"""
        for info in self.account_list:
            if info.character == account.character and info.svr == account.svr:
                info.last_complete_time = datetime.now()
                break

    @model_validator(mode="before")
    @classmethod
    def validator_all(cls, value: Any) -> Any:
        """统一兼容旧配置、列表配置和 OASX 扁平配置。"""
        if not isinstance(value, dict):
            return value

        data = dict(value)
        data.setdefault("account_list", [])
        data.setdefault("private_utilize_config", [])
        data.setdefault("private_forbid_config", [])

        raw_shared = data.get("multi_account_kekkai_utilize_config")
        shared_config = as_dict(raw_shared)
        if not shared_config:
            shared_config = as_dict(data.get("config") or data.get("shared_config"))

        raw_count = data.get("multi_account_kekkai_count_config")
        count_config = as_dict(raw_count)
        if "account_count" not in count_config:
            count_config["account_count"] = data.get(
                "account_count",
                shared_config.get("account_count", 1),
            )
        count_model = (
            raw_count
            if isinstance(raw_count, MultiAccountKekkaiUtilizeCountConfig)
            else MultiAccountKekkaiUtilizeCountConfig(**count_config)
        )

        raw_forbid = data.get("multi_account_kekkai_forbid_config")
        forbid_config = as_dict(raw_forbid)
        if not forbid_config:
            forbid_config = {
                "public_forbid_time_enable": shared_config.get(
                    "public_forbid_time_enable",
                    False,
                ),
                "public_forbid_time_start": shared_config.get(
                    "public_forbid_time_start",
                    time.fromisoformat("00:00:00"),
                ),
                "public_forbid_time_end": shared_config.get(
                    "public_forbid_time_end",
                    time.fromisoformat("00:00:00"),
                ),
            }
        forbid_model = (
            raw_forbid
            if isinstance(raw_forbid, MultiAccountKekkaiUtilizeForbidConfig)
            else MultiAccountKekkaiUtilizeForbidConfig(**forbid_config)
        )

        # 旧配置可能把公共禁止时段和账号数量混在公共蹭卡配置中。
        for key in (
            "account_count",
            "public_forbid_time_enable",
            "public_forbid_time_start",
            "public_forbid_time_end",
        ):
            shared_config.pop(key, None)
        raw_utilize = data.get("multi_account_kekkai_utilize_config")
        utilize_model = (
            raw_utilize
            if isinstance(raw_utilize, MultiAccountKekkaiUtilizeConfig)
            else MultiAccountKekkaiUtilizeConfig(**shared_config)
        )

        data["multi_account_kekkai_count_config"] = count_model
        data["multi_account_kekkai_forbid_config"] = forbid_model
        data["multi_account_kekkai_utilize_config"] = utilize_model
        for alias in (
            "config",
            "shared_config",
            "account_count",
            "public_forbid_time_enable",
            "public_forbid_time_start",
            "public_forbid_time_end",
        ):
            data.pop(alias, None)

        accounts = load_indexed_models(
            data,
            "account_list",
            MultiAccountKekkaiUtilizeAccount,
        )
        private_utilize = load_indexed_models(
            data,
            "private_utilize_config",
            MultiAccountKekkaiUtilizePrivateUtilizeConfig,
        )
        private_forbid = load_indexed_models(
            data,
            "private_forbid_config",
            MultiAccountKekkaiUtilizePrivateForbidConfig,
        )

        pad_parallel_models(
            {
                "account_list": accounts,
                "private_utilize_config": private_utilize,
                "private_forbid_config": private_forbid,
            },
            count_model.account_count,
            {
                "account_list": MultiAccountKekkaiUtilizeAccount,
                "private_utilize_config": MultiAccountKekkaiUtilizePrivateUtilizeConfig,
                "private_forbid_config": MultiAccountKekkaiUtilizePrivateForbidConfig,
            },
        )

        # 未启用私有配置的账号始终回退到公共配置。
        for index, account in enumerate(accounts):
            if not account.enable_private_utilize_config:
                private_utilize[index] = MultiAccountKekkaiUtilizePrivateUtilizeConfig()
            if not account.enable_private_forbid_time:
                private_forbid[index] = MultiAccountKekkaiUtilizePrivateForbidConfig()

        data["account_list"] = accounts
        data["private_utilize_config"] = private_utilize
        data["private_forbid_config"] = private_forbid
        return data

    @model_serializer()
    def serializer_model(self, value: Any) -> dict[str, Any]:
        """把账号列表序列化成前端可以逐组显示的扁平字段。"""
        data: dict[str, Any] = {}
        for key, current_value in self.__dict__.items():
            if key == "account_list":
                serialize_account_list(
                    data,
                    key,
                    current_value,
                    {
                        "private_utilize_config": (
                            self.private_utilize_config,
                            "enable_private_utilize_config",
                            MultiAccountKekkaiUtilizePrivateUtilizeConfig,
                        ),
                        "private_forbid_config": (
                            self.private_forbid_config,
                            "enable_private_forbid_time",
                            MultiAccountKekkaiUtilizePrivateForbidConfig,
                        ),
                    },
                )
                continue

            if key in {"private_utilize_config", "private_forbid_config"}:
                continue

            if isinstance(current_value, list):
                serialize_indexed_models(data, key, current_value)
            else:
                data[key] = dump_model(current_value)
        return data
