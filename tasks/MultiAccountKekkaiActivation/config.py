import re
from datetime import datetime
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
from tasks.Component.SwitchAccount.switch_account_config import AccountInfo
from tasks.Component.config_base import ConfigBase, DateTime
from tasks.Component.config_scheduler import Scheduler
from tasks.KekkaiActivation.config import CardStar, CardType
from tasks.Utils.config_enum import ShikigamiClass


class MultiAccountKekkaiActivationAccount(AccountInfo):
    """多账号挂卡中的账号信息和运行状态。"""

    next_activation_time: DateTime = Field(
        default=DateTime.fromisoformat("2023-01-01 00:00:00"),
        description="每个账号下一次挂卡时间",
    )
    enable_private_config: bool = Field(
        default=False,
        description="是否启用该账号的私有挂卡配置",
    )
    card_not_found_count: int = Field(
        default=0,
        description="该账号连续未发现卡的次数",
    )
    _runtime_card_type: CardType = PrivateAttr(default=CardType.TAIKO)

    @property
    def card_type(self) -> CardType:
        """兼容旧代码读取的运行时卡类型。"""
        return self._runtime_card_type

    @card_type.setter
    def card_type(self, value: CardType) -> None:
        self._runtime_card_type = value


class MultiAccountKekkaiActivationPrivateConfig(ConfigBase):
    """账号私有挂卡配置。"""

    card_type: CardType = Field(default=CardType.TAIKO, description="card_rule_help")
    card_star: CardStar = Field(
        default=CardStar.SIX,
        description="6星：六星及以下、5星：五星及以下",
    )
    swipe_retry_limit: int = Field(
        default=10,
        description="最多滑动X次后触发未找到符合条件的卡",
    )
    min_taiko_num: int = Field(
        default=8,
        description="挂卡太鼓每小时最少收益，低于则不挂卡",
    )
    min_fish_num: int = Field(
        default=16,
        description="挂卡斗鱼每小时最少收益，低于则不挂卡",
    )
    auto_fill: bool = Field(default=False, description="auto_fill_help")
    shikigami_class: ShikigamiClass = Field(
        default=ShikigamiClass.N,
        description="shikigami_class_help",
    )


class MultiAccountKekkaiActivationCountConfig(ConfigBase):
    """多账号挂卡的账号数量配置。"""
    account_count: int = Field(
        default=1,
        ge=1,
        title='账号数量',
        description='账号数量，决定下面生成几组账号配置',
    )

class MultiAccountKekkaiActivationConfig(MultiAccountKekkaiActivationPrivateConfig):
    """多账号挂卡的公共配置。"""

_PRIVATE_CONFIG_FIELDS = frozenset(
    MultiAccountKekkaiActivationPrivateConfig.model_fields.keys()
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
    """提取旧版账号组中的挂卡字段，并记录显式私有开关。"""
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


class MultiAccountKekkaiActivation(ConfigBase, extra="allow"):
    """多账号挂卡的总配置。"""

    scheduler: Scheduler = Field(default_factory=Scheduler)
    multi_account_kekkai_activation_count_config: MultiAccountKekkaiActivationCountConfig = Field(
        default_factory=MultiAccountKekkaiActivationCountConfig,
        title='账号数量',
        description='账号数量，决定下面生成几组账号配置',
    )
    multi_account_kekkai_activation_config: MultiAccountKekkaiActivationConfig = Field(
        default_factory=MultiAccountKekkaiActivationConfig,
        title="公共挂卡配置",
        description="所有账号共用的挂卡参数",
    )
    account_list: list[MultiAccountKekkaiActivationAccount] = Field(default_factory=list)
    private_config: list[MultiAccountKekkaiActivationPrivateConfig] = Field(
        default_factory=list,
    )

    def update_account_next_activation_time(
        self,
        account: MultiAccountKekkaiActivationAccount,
        next_time: datetime,
    ):
        """更新指定账号的下一次挂卡时间。"""
        for info in self.account_list:
            if info.character == account.character and info.svr == account.svr:
                info.next_activation_time = next_time
                break

    def update_account_login_history(
        self,
        account: MultiAccountKekkaiActivationAccount,
    ):
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

        raw_shared = data.get("multi_account_kekkai_activation_config")
        shared_config = as_dict(raw_shared)
        if not shared_config:
            shared_config = as_dict(
                data.get("config")
                or data.get("shared_config")
                or data.get("shared_activation_config")
            )

        raw_count = data.get("multi_account_kekkai_activation_count_config")
        count_config = as_dict(raw_count)
        if "account_count" not in count_config:
            count_config["account_count"] = data.get(
                "account_count",
                shared_config.get("account_count", 1),
            )
        count_model = MultiAccountKekkaiActivationCountConfig(**count_config)

        shared_config.pop("account_count", None)
        # 这两个选项固定开启，不再暴露给使用者配置。
        shared_config.pop("exchange_before", None)
        shared_config.pop("exchange_max", None)
        data["multi_account_kekkai_activation_count_config"] = count_model
        public_model = MultiAccountKekkaiActivationConfig(**shared_config)
        data["multi_account_kekkai_activation_config"] = public_model

        for alias in (
            "config",
            "shared_config",
            "shared_activation_config",
            "account_count",
            "exchange_before",
            "exchange_max",
        ):
            data.pop(alias, None)

        # 把旧版账号组中的挂卡字段迁移到对应的私有配置列表。
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
            MultiAccountKekkaiActivationAccount,
        )
        private_configs = load_indexed_models(
            data,
            "private_config",
            MultiAccountKekkaiActivationPrivateConfig,
        )

        # 旧版账号组默认就是账号独立配置，迁移后自动打开私有配置。
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
                "account_list": MultiAccountKekkaiActivationAccount,
                "private_config": MultiAccountKekkaiActivationPrivateConfig,
            },
        )

        for index, account in enumerate(accounts):
            if not account.enable_private_config:
                private_configs[index] = MultiAccountKekkaiActivationPrivateConfig()
                account.card_type = public_model.card_type
            else:
                account.card_type = private_configs[index].card_type

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
                            MultiAccountKekkaiActivationPrivateConfig,
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
