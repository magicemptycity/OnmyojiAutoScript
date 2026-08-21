import re
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import Field, PrivateAttr, model_serializer, model_validator

from tasks.Component.MultiAccount.multi_account_config import (
    as_dict,
    dump_model,
    load_indexed_models,
    pad_parallel_models,
    serialize_account_list,
    serialize_indexed_models,
)
from tasks.Component.MultiAccount.account_library import MultiAccountReference
from tasks.Component.config_base import ConfigBase, DateTime
from tasks.Component.config_scheduler import Scheduler


class DelegationInterval(str, Enum):
    SIX_HOURS = "六小时循环"
    COMPLETION_TIME = "完成时间循环"


class MultiAccountDelegationAccount(MultiAccountReference):
    """多账号式神委派中的账号信息和运行状态。"""

    next_delegation_time: DateTime = Field(
        default=DateTime.fromisoformat("2023-01-01 00:00:00"),
        description="每个账号下一次委派时间",
    )
    enable_private_config: bool = Field(
        default=False,
        title="是否启用私有配置",
        description="是否启用私有式神委派配置",
    )
    _legacy_config: Any = PrivateAttr(default=None)

    def _get_legacy_flag(self, name: str) -> bool:
        """兼容旧代码读取账号上的委派开关。"""
        config = self._legacy_config
        return bool(getattr(config, name, False))

    @property
    def miyoshino_painting(self) -> bool:
        return self._get_legacy_flag("miyoshino_painting")

    @property
    def bird_feather(self) -> bool:
        return self._get_legacy_flag("bird_feather")

    @property
    def find_earring(self) -> bool:
        return self._get_legacy_flag("find_earring")

    @property
    def cat_boss(self) -> bool:
        return self._get_legacy_flag("cat_boss")

    @property
    def miyoshino(self) -> bool:
        return self._get_legacy_flag("miyoshino")

    @property
    def strange_trace(self) -> bool:
        return self._get_legacy_flag("strange_trace")


class MultiAccountDelegationPrivateConfig(ConfigBase):
    """账号私有的式神委派开关配置。"""

    # 弥助的画-300-六星变异卡，15小时
    miyoshino_painting: bool = Field(default=False, description="miyoshino_painting_help")
    # 鸟之羽-50-20片大蛇的逆鳞，24小时
    bird_feather: bool = Field(default=False, description="bird_feather_help")
    # 寻找耳环-300-金币28万，15小时
    find_earring: bool = Field(default=False, description="find_earring_help")
    # 猫老大-300-四星白蛋，12小时
    cat_boss: bool = Field(default=False, description="cat_boss_help")
    # 接送弥助-100-三星结界卡，8小时
    miyoshino: bool = Field(default=False, description="miyoshino_help")
    # 奇怪的痕迹-100-金币九万八，15小时
    strange_trace: bool = Field(default=False, description="strange_trace_help")


class MultiAccountDelegationCountConfig(ConfigBase):
    """多账号式神委派的账号数量和调度间隔配置。"""

    account_count: int = Field(
        default=1,
        ge=1,
        title="账号数量",
        description="账号数量，决定下面生成几组账号配置",
    )
    delegation_interval: DelegationInterval = Field(
        default=DelegationInterval.SIX_HOURS,
        title="委派间隔",
        description="委派间隔：六小时循环|完成时间循环",
    )


class MultiAccountDelegationConfig(
    MultiAccountDelegationPrivateConfig,
    extra="allow",
):
    """多账号式神委派的公共配置。"""


_PRIVATE_CONFIG_FIELDS = frozenset(
    MultiAccountDelegationPrivateConfig.model_fields.keys()
)


def _collect_raw_account_entries(data: dict) -> dict[int, Any]:
    """收集列表和扁平字段中的原始账号配置，用于旧配置迁移。"""
    entries: dict[int, Any] = {}
    raw_list = data.get("account_list")
    if isinstance(raw_list, list):
        entries.update(enumerate(raw_list))

    pattern = re.compile(r"^account_list_(\d+)$")
    for key, value in data.items():
        match = pattern.fullmatch(key)
        if match:
            entries[int(match.group(1)) - 1] = value
    return entries


def _extract_legacy_private_configs(data: dict) -> tuple[dict[int, dict], set[int]]:
    """提取旧版账号组中的委派开关，并记录显式私有开关。"""
    legacy_configs: dict[int, dict] = {}
    explicit_flags: set[int] = set()
    for index, value in _collect_raw_account_entries(data).items():
        if not isinstance(value, dict):
            continue
        if "enable_private_config" in value:
            explicit_flags.add(index)
        private_config = {
            key: value[key]
            for key in _PRIVATE_CONFIG_FIELDS
            if key in value
        }
        if private_config:
            legacy_configs[index] = private_config
    return legacy_configs, explicit_flags


class MultiAccountDelegation(ConfigBase, extra="allow"):
    """多账号式神委派的总配置。"""

    scheduler: Scheduler = Field(default_factory=Scheduler)
    multi_account_delegation_count_config: MultiAccountDelegationCountConfig = Field(
        default_factory=MultiAccountDelegationCountConfig,
        title="账号数量与委派间隔",
        description="设置多账号委派的账号数量和账号下一次委派的间隔",
    )
    multi_account_delegation_config: MultiAccountDelegationConfig = Field(
        default_factory=MultiAccountDelegationConfig,
        title="公共式神委派配置",
        description="所有账号共用的式神委派参数",
    )
    account_list: list[MultiAccountDelegationAccount] = Field(default_factory=list)
    private_config: list[MultiAccountDelegationPrivateConfig] = Field(
        default_factory=list,
    )

    def update_account_next_delegation_time(
        self,
        account: MultiAccountDelegationAccount,
        next_time: datetime,
    ) -> None:
        """更新指定账号的下一次委派时间。"""
        for info in self.account_list:
            if info.character == account.character and info.svr == account.svr:
                info.next_delegation_time = next_time
                break

    def update_account_login_history(self, account: MultiAccountDelegationAccount) -> None:
        """更新账号最近一次完成时间。"""
        for info in self.account_list:
            if info.character == account.character and info.svr == account.svr:
                info.last_complete_time = datetime.now()
                break

    @model_validator(mode="before")
    @classmethod
    def validator_all(cls, value: Any) -> Any:
        """兼容旧版账号配置，并还原 OASX 的扁平配置组。"""
        if not isinstance(value, dict):
            return value

        data = dict(value)
        data.setdefault("account_list", [])
        data.setdefault("private_config", [])
        legacy_configs, explicit_flags = _extract_legacy_private_configs(data)

        raw_shared = data.get("multi_account_delegation_config")
        shared_config = as_dict(raw_shared)
        if not shared_config:
            shared_config = as_dict(
                data.get("config")
                or data.get("shared_config")
                or data.get("common_config")
            )

        raw_count = data.get("multi_account_delegation_count_config")
        count_config = as_dict(raw_count)
        if "account_count" not in count_config:
            count_config["account_count"] = data.get(
                "account_count",
                shared_config.get("account_count", 1),
            )
        if "delegation_interval" not in count_config:
            count_config["delegation_interval"] = data.get(
                "delegation_interval",
                shared_config.get(
                    "delegation_interval",
                    DelegationInterval.SIX_HOURS,
                ),
            )
        try:
            count_config["account_count"] = int(count_config["account_count"])
        except (TypeError, ValueError):
            count_config["account_count"] = 1
        count_model = MultiAccountDelegationCountConfig(**count_config)

        shared_config.pop("account_count", None)
        shared_config.pop("delegation_interval", None)
        public_model = MultiAccountDelegationConfig(**shared_config)
        data["multi_account_delegation_count_config"] = count_model
        data["multi_account_delegation_config"] = public_model

        for alias in (
            "config",
            "shared_config",
            "common_config",
            "account_count",
            "delegation_interval",
        ):
            data.pop(alias, None)

        # 把旧版账号组中的委派开关迁移到对应的私有配置列表。
        private_values = data.get("private_config")
        if not isinstance(private_values, list):
            private_values = []
        else:
            private_values = list(private_values)
        for index, private_config in legacy_configs.items():
            while len(private_values) <= index:
                private_values.append({})
            if not private_values[index]:
                private_values[index] = private_config
        data["private_config"] = private_values

        accounts = load_indexed_models(
            data,
            "account_list",
            MultiAccountDelegationAccount,
        )
        private_configs = load_indexed_models(
            data,
            "private_config",
            MultiAccountDelegationPrivateConfig,
        )

        # 旧版账号组默认就是账号独立配置，迁移后自动启用私有配置。
        for index in legacy_configs:
            if index < len(accounts) and index not in explicit_flags:
                accounts[index].enable_private_config = True

        pad_parallel_models(
            {
                "account_list": accounts,
                "private_config": private_configs,
            },
            count_model.account_count,
            {
                "account_list": MultiAccountDelegationAccount,
                "private_config": MultiAccountDelegationPrivateConfig,
            },
        )

        # 未启用私有配置的账号不参与私有配置序列化和运行。
        for index, account in enumerate(accounts):
            if not account.enable_private_config:
                private_configs[index] = MultiAccountDelegationPrivateConfig()
                account._legacy_config = public_model
            else:
                account._legacy_config = private_configs[index]

        data["account_list"] = accounts
        data["private_config"] = private_configs
        return data

    @model_serializer()
    def serializer_model(self, value: Any) -> dict[str, Any]:
        """把账号列表和私有配置序列化成前端配置组。"""
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
                            MultiAccountDelegationPrivateConfig,
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
