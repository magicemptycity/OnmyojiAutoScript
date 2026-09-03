from __future__ import annotations

from module.logger import logger
from tasks.Component.SwitchAccount.netease_account_ui import (
    AccountUiUnavailable,
    NeteaseAccountUi,
)
from tasks.Component.SwitchAccount.switch_account import SwitchAccount


class TreeSwitchAccount(SwitchAccount):
    """使用网易登录控件树选择账号的切号方式。

    多账号多任务新和多账号多任务定时仅使用控件树读取、匹配和选择已保存账号；
    控件树不可用或未找到账号时直接失败，不再回退到 OCR 账号列表。
    服务器、角色选择以及登录后的弹窗处理仍沿用原有切号流程。
    """

    def _netease_account_ui(self) -> NeteaseAccountUi:
        return NeteaseAccountUi(self.device)

    @staticmethod
    def _account_matches(account_info, actual: str | None) -> bool:
        """兼容 AccountInfo 与多账号配置模型的账号、别名匹配规则。"""
        actual = NeteaseAccountUi.normalize_account(actual)
        account = NeteaseAccountUi.normalize_account(getattr(account_info, "account", ""))
        if not actual or not account:
            return False
        account_prefix = account.split("@", 1)[0]
        if actual == account or actual.startswith(account_prefix):
            return True

        aliases = str(getattr(account_info, "account_alias", "") or "")
        for alias in aliases.split("#"):
            normalized_alias = NeteaseAccountUi.normalize_account(alias)
            if normalized_alias and actual.startswith(normalized_alias):
                return True
        return False

    def is_account_selected(self, accountInfo) -> bool:
        if not getattr(accountInfo, "account", ""):
            return False
        try:
            selected = self._netease_account_ui().current_account()
        except AccountUiUnavailable as exc:
            logger.error("网易账号控件树不可用，无法确认当前账号：%s", exc)
            return False
        return self._account_matches(accountInfo, selected)

    def selectAccount(self, accountInfo) -> bool:
        """仅通过控件树定位并确认目标账号，不使用 OCR 账号列表。"""
        if not getattr(accountInfo, "account", ""):
            return False
        try:
            selected = self._netease_account_ui().select_account(
                accountInfo.account,
                matcher=lambda actual: self._account_matches(accountInfo, actual),
            )
        except AccountUiUnavailable as exc:
            logger.error("网易账号控件树不可用，无法选择账号：%s", exc)
            return False
        if selected:
            logger.info("已通过网易账号控件树选中目标账号")
        else:
            logger.warning("网易账号控件树中未找到目标账号，不回退 OCR 选账号")
        return selected
