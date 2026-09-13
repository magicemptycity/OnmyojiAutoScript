from datetime import datetime
from typing import Any

from pydantic import Field

from tasks.Component.MultiAccount.shared_public_accounts import SharedPublicAccount
from tasks.Component.config_base import ConfigBase
from tasks.MultiAccountTaskOrchestration.config import MultiAccountTaskConfigMode


class ScheduledAccountBase(ConfigBase, extra="allow"):
    """账号独立调度功能共用的公共账号快照与配置来源字段。"""

    public_account_identifier: str = Field(default="", title="公共账号标识")
    character: str = Field(default="")
    svr: str = Field(default="")
    account: str = Field(default="")
    account_alias: str = Field(default="")
    apple_or_android: bool = Field(default=True)
    enabled: bool = Field(
        default=True,
        title="启用该功能账号",
        description="停用后该功能跳过此账号，并保留调度器、禁止时段和私有配置。",
    )
    last_complete_time: datetime = Field(default=datetime(2023, 1, 1))
    config_mode: MultiAccountTaskConfigMode = Field(
        default=MultiAccountTaskConfigMode.PRIVATE,
        title="配置来源",
    )
    private_config: dict[str, Any] = Field(
        default_factory=dict,
        json_schema_extra={"default": {}},
    )

    def is_valid(self) -> bool:
        return bool(
            self.public_account_identifier.strip()
            and self.character.strip()
            and self.svr.strip()
        )

    def sync_public_account(self, source: SharedPublicAccount) -> None:
        self.public_account_identifier = source.identifier.strip()
        self.character = source.character
        self.svr = source.svr
        self.account = source.account
        self.account_alias = source.account_alias
        self.apple_or_android = source.apple_or_android
