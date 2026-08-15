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
from tasks.Component.MultiAccount.account_library import MultiAccountReference
from tasks.Component.config_base import ConfigBase, DateTime, Time
from tasks.Component.config_scheduler import Scheduler
from tasks.KekkaiUtilize.config import SelectFriendList, UtilizeRule
from tasks.Utils.config_enum import ShikigamiClass


class MultiAccountKekkaiUtilizeAccount(MultiAccountReference):
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


class MultiAccountKekkaiUtilizeBaseConfig(ConfigBase):
    """公共和私有蹭卡共用的配置字段。"""

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


class MultiAccountKekkaiUtilizeConfig(MultiAccountKekkaiUtilizeBaseConfig, extra="allow"):
    """多账号蹭卡的公共结界配置。"""


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
    public_forbid_time_start_1: Time = Field(
        default=time.fromisoformat("00:00:00"),
        title="禁止蹭卡开始时间 1",
        description="公共禁止蹭卡时间段 1 开始时间",
    )
    public_forbid_time_end_1: Time = Field(
        default=time.fromisoformat("00:00:00"),
        title="禁止蹭卡结束时间 1",
        description="公共禁止蹭卡时间段 1 结束时间",
    )
    public_forbid_time_start_2: Time = Field(
        default=time.fromisoformat("00:00:00"),
        title="禁止蹭卡开始时间 2",
        description="公共禁止蹭卡时间段 2 开始时间",
    )
    public_forbid_time_end_2: Time = Field(
        default=time.fromisoformat("00:00:00"),
        title="禁止蹭卡结束时间 2",
        description="公共禁止蹭卡时间段 2 结束时间",
    )

    def get_forbid_windows(self) -> list[tuple[time, time]]:
        windows = []
        for index in range(1, 3):
            start = getattr(self, f"public_forbid_time_start_{index}", None)
            end = getattr(self, f"public_forbid_time_end_{index}", None)
            if start is None or end is None:
                continue
            if start == end:
                continue
            windows.append((start, end))
        return windows

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_fields(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value

        data = dict(value)
        legacy_start = data.pop("public_forbid_time_start", None)
        legacy_end = data.pop("public_forbid_time_end", None)
        if "public_forbid_time_start_1" not in data and legacy_start is not None:
            data["public_forbid_time_start_1"] = legacy_start
        if "public_forbid_time_end_1" not in data and legacy_end is not None:
            data["public_forbid_time_end_1"] = legacy_end
        return data


class MultiAccountKekkaiUtilizePrivateUtilizeConfig(MultiAccountKekkaiUtilizeBaseConfig):
    """多账号蹭卡的账号私有配置。"""


class MultiAccountKekkaiUtilizePrivateForbidConfig(ConfigBase):
    """账号私有禁止蹭卡时段配置。"""

    utilize_forbidden_time_start_1: Time = Field(
        default=time.fromisoformat("00:00:00"),
        description="账号禁止蹭卡时间段 1 开始时间",
    )
    utilize_forbidden_time_end_1: Time = Field(
        default=time.fromisoformat("00:00:00"),
        description="账号禁止蹭卡时间段 1 结束时间",
    )
    utilize_forbidden_time_start_2: Time = Field(
        default=time.fromisoformat("00:00:00"),
        description="账号禁止蹭卡时间段 2 开始时间",
    )
    utilize_forbidden_time_end_2: Time = Field(
        default=time.fromisoformat("00:00:00"),
        description="账号禁止蹭卡时间段 2 结束时间",
    )

    def get_forbid_windows(self) -> list[tuple[time, time]]:
        windows = []
        for index in range(1, 3):
            start = getattr(self, f"utilize_forbidden_time_start_{index}", None)
            end = getattr(self, f"utilize_forbidden_time_end_{index}", None)
            if start is None or end is None:
                continue
            if start == end:
                continue
            windows.append((start, end))
        return windows

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_fields(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value

        data = dict(value)
        legacy_start = data.pop("utilize_forbidden_time_start", None)
        legacy_end = data.pop("utilize_forbidden_time_end", None)
        # 旧版私有开关由外层账号配置接管，此处只迁移时间字段。
        data.pop("utilize_forbidden_time_enable", None)
        if "utilize_forbidden_time_start_1" not in data and legacy_start is not None:
            data["utilize_forbidden_time_start_1"] = legacy_start
        if "utilize_forbidden_time_end_1" not in data and legacy_end is not None:
            data["utilize_forbidden_time_end_1"] = legacy_end
        return data


def _collect_indexed_values(data: dict, field_name: str) -> dict[int, Any]:
    """收集列表字段和扁平字段中的配置项。"""
    values: dict[int, Any] = {}
    raw_values = data.get(field_name)
    if isinstance(raw_values, list):
        values.update(enumerate(raw_values))

    prefix = f"{field_name}_"
    for key, value in data.items():
        if not key.startswith(prefix):
            continue
        suffix = key[len(prefix):]
        if suffix.isdigit() and int(suffix) > 0:
            values[int(suffix) - 1] = value
    return values


def _collect_legacy_private_forbid_flags(
    data: dict,
) -> tuple[dict[int, bool], set[int]]:
    """读取旧版私有禁止时段开关及账号开关的显式配置。"""
    legacy_flags: dict[int, bool] = {}
    for index, value in _collect_indexed_values(data, "private_forbid_config").items():
        if isinstance(value, dict) and "utilize_forbidden_time_enable" in value:
            legacy_flags[index] = bool(value["utilize_forbidden_time_enable"])

    explicit_account_flags = {
        index
        for index, value in _collect_indexed_values(data, "account_list").items()
        if isinstance(value, dict) and "enable_private_forbid_time" in value
    }
    return legacy_flags, explicit_account_flags


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
        legacy_private_forbid_flags, explicit_account_flags = (
            _collect_legacy_private_forbid_flags(data)
        )

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
                "public_forbid_time_start_1": shared_config.get(
                    "public_forbid_time_start_1",
                    shared_config.get(
                        "public_forbid_time_start",
                        time.fromisoformat("00:00:00"),
                    ),
                ),
                "public_forbid_time_end_1": shared_config.get(
                    "public_forbid_time_end_1",
                    shared_config.get(
                        "public_forbid_time_end",
                        time.fromisoformat("00:00:00"),
                    ),
                ),
                "public_forbid_time_start_2": shared_config.get(
                    "public_forbid_time_start_2",
                    time.fromisoformat("00:00:00"),
                ),
                "public_forbid_time_end_2": shared_config.get(
                    "public_forbid_time_end_2",
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
            "public_forbid_time_start_1",
            "public_forbid_time_end_1",
            "public_forbid_time_start_2",
            "public_forbid_time_end_2",
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
            "public_forbid_time_start_1",
            "public_forbid_time_end_1",
            "public_forbid_time_start_2",
            "public_forbid_time_end_2",
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

        for index, enabled in legacy_private_forbid_flags.items():
            if index < len(accounts) and index not in explicit_account_flags:
                accounts[index].enable_private_forbid_time = enabled

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
