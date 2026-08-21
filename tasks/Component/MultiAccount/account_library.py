from typing import Any

from pydantic import BaseModel, Field, model_serializer, model_validator

from tasks.Component.SwitchAccount.switch_account_config import AccountInfo
from tasks.Component.config_base import ConfigBase, dynamic_hide
from tasks.Component.config_scheduler import Scheduler
from tasks.Component.MultiAccount.multi_account_config import (
    as_dict,
    dump_model,
    load_indexed_models,
    pad_parallel_models,
    serialize_indexed_models,
)


class SharedAccount(BaseModel):
    """公共账号库中的登录信息。"""

    character: str = Field(default="", description="character_help")
    svr: str = Field(default="", description="svr_help")
    account: str = Field(default="", description="account_help")
    account_alias: str = Field(default="", description="account_alias_help")
    apple_or_android: bool = Field(default=True, description="apple_or_android_help")

    @model_validator(mode="before")
    @classmethod
    def reset_dependent_fields_when_empty(cls, value: Any) -> Any:
        """加载配置时，角色名为空则清空同一账号的其余信息。"""
        if not isinstance(value, dict):
            return value
        data = dict(value)
        if not str(data.get("character", "") or "").strip():
            data["character"] = ""
            data["svr"] = ""
            data["account"] = ""
            data["account_alias"] = ""
            data["apple_or_android"] = True
        return data

    def __setattr__(self, name: str, value: Any) -> None:
        """页面清空角色名时，立即恢复账号组默认值。"""
        super().__setattr__(name, value)
        if name == "character" and not str(value or "").strip():
            super().__setattr__("svr", "")
            super().__setattr__("account", "")
            super().__setattr__("account_alias", "")
            super().__setattr__("apple_or_android", True)

    def is_valid(self) -> bool:
        return bool(self.character and self.svr)


class MultiAccountReference(AccountInfo):
    """多账号任务的账号引用；0 表示继续使用本任务内填写的旧账号信息。"""

    shared_account_index: int = Field(
        default=0,
        ge=0,
        le=99,
        description="公共账号序号；填写后自动使用公共账号库对应账号，0 表示使用本任务内旧账号信息",
    )

    # 账号信息由公共账号库统一维护，任务页面只显示公共账号序号。
    hide_fields = dynamic_hide(
        "character", "svr", "account", "account_alias", "apple_or_android"
    )

    def is_valid(self):
        return self.shared_account_index > 0 or super().is_valid()


class MultiAccountAccountsConfig(ConfigBase):
    account_count: int = Field(default=1, ge=1, le=99, description="公共账号库中的账号数量")


class MultiAccountAccounts(ConfigBase, extra="allow"):
    """全部多账号功能共用的账号库。"""

    scheduler: Scheduler = Field(default_factory=Scheduler)
    multi_account_accounts_config: MultiAccountAccountsConfig = Field(default_factory=MultiAccountAccountsConfig)
    account_list: list[SharedAccount] = Field(default_factory=list)

    @classmethod
    def _normalize(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        raw = data.get("multi_account_accounts_config")
        config = as_dict(raw)
        config.setdefault("account_count", data.get("account_count", 1))
        count = MultiAccountAccountsConfig(**config).account_count
        data["multi_account_accounts_config"] = MultiAccountAccountsConfig(**config)
        data.pop("account_count", None)
        accounts = load_indexed_models(data, "account_list", SharedAccount)
        pad_parallel_models({"account_list": accounts}, count, {"account_list": SharedAccount})
        data["account_list"] = accounts
        return data

    @model_validator(mode="before")
    @classmethod
    def validator_all(cls, value: Any) -> Any:
        return cls._normalize(value)

    @model_serializer()
    def serializer_model(self, value: Any) -> dict[str, Any]:
        data = {}
        for key, current_value in self.__dict__.items():
            if isinstance(current_value, list):
                serialize_indexed_models(data, key, current_value)
            else:
                data[key] = dump_model(current_value)
        return data


def resolve_shared_account(config: Any, account: AccountInfo) -> bool:
    """将公共账号库信息填充到任务账号对象；返回引用是否有效。"""
    index = getattr(account, "shared_account_index", 0)
    if not index:
        return True
    library = getattr(config, "multi_account_accounts", None)
    accounts = getattr(library, "account_list", []) if library else []
    if index < 1 or index > len(accounts):
        return False
    shared = accounts[index - 1]
    if not shared.is_valid():
        return False
    account.character = shared.character
    account.svr = shared.svr
    account.account = shared.account
    account.account_alias = shared.account_alias
    account.apple_or_android = shared.apple_or_android
    return True
