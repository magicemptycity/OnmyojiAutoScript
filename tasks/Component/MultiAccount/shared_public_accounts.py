"""供新版多账号功能复用的公共账号库。"""

from typing import Any

from pydantic import Field, model_serializer, model_validator

from tasks.Component.MultiAccount.multi_account_config import (
    load_indexed_models,
    pad_parallel_models,
    serialize_indexed_models,
)
from tasks.Component.config_base import ConfigBase


class SharedPublicAccount(ConfigBase, extra="allow"):
    """新版多账号功能共用的一条公共登录账号。"""

    identifier: str = Field(default="", title="账号标识", description="公共账号库内唯一的自定义标识，不能为空且不可重复。")
    character: str = Field(default="", description="character_help")
    svr: str = Field(default="", description="svr_help")
    account: str = Field(default="", description="account_help")
    account_alias: str = Field(default="", description="account_alias_help")
    apple_or_android: bool = Field(default=True, description="apple_or_android_help")

    def is_valid(self) -> bool:
        return bool(self.identifier.strip() and self.character.strip() and self.svr.strip())


class SharedPublicAccounts(ConfigBase, extra="allow"):
    """供“多账号多任务新”及后续新版多账号功能复用的公共账号库。"""

    account_count: int = Field(default=1, ge=1, title="公共账号数量")
    account_list: list[SharedPublicAccount] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def validator_all(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        try:
            count = max(1, int(data.get("account_count", 1)))
        except (TypeError, ValueError):
            count = 1
        accounts = load_indexed_models(data, "account_list", SharedPublicAccount)
        accounts = [account for account in accounts if account.identifier.strip()]
        pad_parallel_models({"account_list": accounts}, count, {"account_list": SharedPublicAccount})
        data["account_count"] = count
        data["account_list"] = accounts
        return data

    @model_serializer()
    def serializer_model(self, value: Any) -> dict[str, Any]:
        data: dict[str, Any] = {"account_count": self.account_count}
        serialize_indexed_models(data, "account_list", self.account_list)
        return data

    def find(self, identifier: str) -> SharedPublicAccount | None:
        normalized = identifier.strip()
        return next((item for item in self.account_list if item.identifier.strip() == normalized), None)
