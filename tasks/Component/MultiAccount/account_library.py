import re
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_serializer, model_validator

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
    account_identifier: str = Field(
        default="",
        max_length=32,
        title="自定义账号标识",
        description="只能填写字母和数字，且必须同时包含字母和数字；留空时默认使用本账号在公共账号列表中的序号。",
    )
    apple_or_android: bool = Field(default=True, description="apple_or_android_help")

    @field_validator("account_identifier", mode="before")
    @classmethod
    def validate_account_identifier(cls, value: Any) -> str:
        """校验公共账号的自定义标识。"""
        identifier = str(value or "").strip()
        if not identifier:
            return ""
        if not re.fullmatch(r"(?=.*[A-Za-z])(?=.*\d)[A-Za-z0-9]+", identifier):
            raise ValueError("自定义账号标识只能由字母和数字组成，且必须同时包含字母和数字")
        return identifier

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
            data["account_identifier"] = ""
            data["apple_or_android"] = True
        return data

    def __setattr__(self, name: str, value: Any) -> None:
        """页面清空角色名时，立即恢复账号组默认值。"""
        super().__setattr__(name, value)
        if name == "character" and not str(value or "").strip():
            super().__setattr__("svr", "")
            super().__setattr__("account", "")
            super().__setattr__("account_alias", "")
            super().__setattr__("account_identifier", "")
            super().__setattr__("apple_or_android", True)

    def is_valid(self) -> bool:
        return bool(self.character and self.svr)


class MultiAccountReference(AccountInfo):
    """多账号任务的账号引用；兼容旧配置中的公共账号序号。"""

    shared_account_identifier: str = Field(
        default="",
        max_length=32,
        title="公共账号标识",
        description="填写公共账号的自定义标识；公共账号未填写自定义标识时，填写其列表序号。留空则使用本任务内旧账号信息。",
    )

    @model_validator(mode="before")
    @classmethod
    def migrate_shared_account_index(cls, value: Any) -> Any:
        """将旧的公共账号序号转换为新的公共账号标识。"""
        if not isinstance(value, dict):
            return value
        data = dict(value)
        identifier = data.get("shared_account_identifier")
        if identifier is None:
            legacy_index = data.get("shared_account_index", 0)
            identifier = "" if str(legacy_index or "").strip() in {"", "0"} else str(legacy_index).strip()
        data["shared_account_identifier"] = str(identifier or "").strip()
        data.pop("shared_account_index", None)
        return data

    @field_validator("shared_account_identifier", mode="before")
    @classmethod
    def validate_shared_account_identifier(cls, value: Any) -> str:
        """公共账号引用允许字母数字标识，纯数字表示旧的列表序号。"""
        identifier = str(value or "").strip()
        if not identifier or identifier == "0":
            return ""
        if not re.fullmatch(r"[A-Za-z0-9]+", identifier):
            raise ValueError("公共账号标识只能由字母和数字组成")
        return identifier

    # 账号信息由公共账号库统一维护，任务页面只显示公共账号标识。
    hide_fields = dynamic_hide(
        "character", "svr", "account", "account_alias", "apple_or_android"
    )

    def is_valid(self):
        return bool(self.shared_account_identifier) or super().is_valid()


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

    @field_validator("account_list")
    @classmethod
    def validate_unique_account_identifiers(cls, accounts: list[SharedAccount]) -> list[SharedAccount]:
        """有效公共账号的实际标识必须唯一，避免引用到错误账号。"""
        identifiers: dict[str, int] = {}
        for index, account in enumerate(accounts, start=1):
            if not account.is_valid():
                continue
            identifier = get_shared_account_identifier(account, index)
            normalized_identifier = identifier.casefold()
            if normalized_identifier in identifiers:
                previous_index = identifiers[normalized_identifier]
                raise ValueError(
                    f"公共账号标识重复：第 {previous_index} 个和第 {index} 个账号均为 {identifier}"
                )
            identifiers[normalized_identifier] = index
        return accounts

    @model_serializer()
    def serializer_model(self, value: Any) -> dict[str, Any]:
        data = {}
        for key, current_value in self.__dict__.items():
            if isinstance(current_value, list):
                serialize_indexed_models(data, key, current_value)
            else:
                data[key] = dump_model(current_value)
        return data


def get_shared_account_identifier(account: SharedAccount, index: int) -> str:
    """返回公共账号的实际标识：优先自定义标识，否则使用列表序号。"""
    return account.account_identifier or str(index)


def resolve_shared_account(config: Any, account: AccountInfo) -> bool:
    """按公共账号标识填充登录信息；返回引用是否有效。"""
    identifier = str(getattr(account, "shared_account_identifier", "") or "").strip()
    if not identifier:
        return True

    library = getattr(config, "multi_account_accounts", None)
    accounts = getattr(library, "account_list", []) if library else []
    matched_accounts = [
        item
        for index, item in enumerate(accounts, start=1)
        if item.is_valid()
        and get_shared_account_identifier(item, index).casefold() == identifier.casefold()
    ]
    # 即使配置页面尚未重新加载，也不能在重复标识中随意选择其中一个账号。
    if len(matched_accounts) != 1:
        return False
    shared = matched_accounts[0]

    account.character = shared.character
    account.svr = shared.svr
    account.account = shared.account
    account.account_alias = shared.account_alias
    account.apple_or_android = shared.apple_or_android
    return True
