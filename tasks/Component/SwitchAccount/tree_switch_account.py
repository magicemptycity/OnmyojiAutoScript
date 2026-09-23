from __future__ import annotations

import time

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

    def _account_ui(self) -> NeteaseAccountUi:
        return self._netease_account_ui()

    def login(self, accountInfo) -> bool:
        """新多账号专用登录流程：网易账号阶段只使用控件树。"""
        is_account_logon = False
        character_selected = False
        account_selected = False
        login_button_clicked = False
        for _ in range(120):
            self.screenshot()
            ui = self._account_ui()
            root = ui.dump()

            # 本轮状态判断复用同一份控件树；点击后由 click_resource 重新读取。
            # 登录平台页：只处理控件树，不依赖平台图片。
            platform_id = (
                "netease_mpay__android"
                if getattr(accountInfo, "apple_or_android", True)
                else "netease_mpay__ios"
            )
            if (node := ui.clickable_node(root, platform_id)) is not None and ui.click_node(
                node, f"网易登录平台-{platform_id}"
            ):
                is_account_logon = True
                time.sleep(2)
                continue

            # 登录账号页：即使账号列表尚未展开，也要先点击当前账号项展开列表，
            # 再由 NeteaseAccountUi 完成滚动、匹配和确认。
            try:
                account_list_visible = bool(ui.nodes(root, "netease_mpay__user_list"))
                account_page_visible = bool(ui.nodes(root, "netease_mpay__login_user_item"))
                if account_list_visible or account_page_visible:
                    # 账号已选中后，账号列表节点可能在页面过渡期间短暂残留，
                    # 不得再次 selectAccount，否则会把正常过渡误判成“找不到账号”。
                    if not account_selected:
                        if not self.selectAccount(accountInfo):
                            logger.error("控件树中未找到目标网易账号：%s", getattr(accountInfo, "account", ""))
                            return False
                        account_selected = True
                        time.sleep(0.5)
                    if not login_button_clicked:
                        if not self._account_ui().click_resource("netease_mpay__login", "网易账号登录"):
                            logger.error("控件树中未找到网易登录按钮")
                            return False
                        login_button_clicked = True
                    time.sleep(1)
                    continue
            except AccountUiUnavailable as exc:
                logger.error("网易账号控件树不可用：%s", exc)
                return False

            # 用户中心页：控件树读取当前账号并点击切换账号。
            try:
                current = ui.current_user_center_account(root)
                user_center_visible = bool(ui.nodes(root, "netease_mpay__switch_account"))
                if current is not None and self._account_matches(accountInfo, current):
                    is_account_logon = True
                    if self._account_ui().click_resource("netease_mpay__close_window", "关闭网易用户中心"):
                        time.sleep(0.5)
                    continue
                # 当前账号文本偶尔尚未加载，但用户中心切换按钮已存在时，
                # 仍应直接打开账号列表，不能回退到图片点击后卡住。
                if user_center_visible and self._account_ui().click_resource(
                    "netease_mpay__switch_account", "网易用户中心-切换账号"
                ):
                    is_account_logon = False
                    account_selected = False
                    login_button_clicked = False
                    time.sleep(1)
                    continue
            except AccountUiUnavailable as exc:
                logger.error("网易用户中心控件树不可用：%s", exc)
                return False

            # 登录页没有进入用户中心时，点击原有用户中心入口；
            # 这是从庭院或重新登录页进入账号切换流程的必经步骤。
            # 网易游戏登录页/账号选择页顶部会显示网易 LOGO；
            # 已在该流程中时禁止再次点击游戏内用户中心入口。
            login_surface_visible = self.appear(self.I_SA_NETEASE_GAME_LOGO)
            if (
                not is_account_logon
                and not login_surface_visible
                and self.click(self.C_SA_LOGIN_FORM_USER_CENTER, interval=1)
            ):
                time.sleep(1)
                continue

            # 回到阴阳师后，服务器和角色仍沿用原有 OCR/图片识别。
            if is_account_logon and not character_selected:
                if self.select_target(accountInfo.character, accountInfo.svr):
                    character_selected = True
                    time.sleep(1)
                    continue
            if is_account_logon and character_selected:
                logger.info("character %s-%s account:%s login Success", accountInfo.character, accountInfo.svr, accountInfo.account)
                return True
            time.sleep(0.5)

        logger.error("character %s-%s account:%s login Failed", accountInfo.character, accountInfo.svr, accountInfo.account)
        return False

    def _account_info_matches(self, accountInfo, actual: str | None) -> bool:
        """兼容新多账号账号模型在用户中心页面的账号匹配。"""
        return self._account_matches(accountInfo, actual)

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
