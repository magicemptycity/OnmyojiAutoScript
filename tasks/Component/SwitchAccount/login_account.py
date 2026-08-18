import math
import time

from module.atom.click import RuleClick
from module.atom.gif import RuleGif
from module.atom.image import RuleImage
from module.atom.ocr import RuleOcr
from module.image.operators import threshold_bgr_to_inverted_rgb
from module.logger import logger
from tasks.Component.SwitchAccount.assets import SwitchAccountAssets
from tasks.Component.SwitchAccount.switch_account_config import AccountInfo
from tasks.base_task import BaseTask


class LoginAccount(BaseTask, SwitchAccountAssets):

    def get_svr_name(self):
        self.screenshot()
        ocrRes = self.O_SA_LOGIN_FORM_SVR_NAME.ocr(self.device.image)
        return ocrRes

    def select_target(self, characterName: str, svrName: str = None) -> bool:
        """
        统一的选择目标方法：优先匹配服务器名（若提供），其次匹配角色名。
        服务器名匹配策略：完全匹配 → 删首匹配 → 删尾匹配 → 字符集交集模糊匹配(阈值0.5)
        角色名匹配策略：完全匹配 → 删首匹配 → 删尾匹配
        服务器名匹配时点击下方30px，角色名匹配时点击上方30px。

        注意：如果提供了服务器名，会先检查当前登录界面的服务器是否已是目标服务器。
            若是则直接返回 True（不进行任何点击），这意味着调用者应认为角色已选中。
            请确保在 login() 中调用时，此行为符合预期（例如，如果服务器已正确，
            但角色尚未选中，则直接返回 True 可能导致角色未被实际点击选中）。

        @param characterName: 目标角色名
        @param svrName: 目标服务器名（可选）
        @return: True 表示选中成功，False 表示失败
        """
        logger.info("start select_target: character=%s, svr=%s", characterName, svrName)

        # ---------- 保留原 switch_svr 的当前服务器检查 ----------
        if svrName:
            self.O_SA_LOGIN_FORM_SVR_NAME.keyword = svrName
            self.screenshot()
            if self.ocr_appear(self.O_SA_LOGIN_FORM_SVR_NAME):
                logger.info("current svr is %s, no need to switch", svrName)
                return True
        # ------------------------------------------------------

        # 点击“切换服务器”按钮，进入选择区域界面
        self.ui_click(self.C_SA_LOGIN_FORM_SWITCH_SVR_BTN, self.I_SA_CHECK_SELECT_SVR_4, interval=3)

        # ---------- 辅助匹配函数 ----------
        def exact_match(target, candidates):
            """完全匹配"""
            for idx, cand in enumerate(candidates):
                if cand == target:
                    return idx
            return None

        def fuzzy_match(target, candidates, threshold=0.5):
            """字符集交集相似度匹配（原switch_svr逻辑）"""
            for idx, cand in enumerate(candidates):
                if len(cand) < 3:
                    continue
                intersection = set(target).intersection(set(cand))
                similarity = len(intersection) / max(len(target), len(cand))
                if similarity > threshold:
                    return idx
            return None

        def trim_first_match(target, candidates):
            """删首字符匹配"""
            if len(target) <= 1:
                return None
            target_trim = target[1:]
            for idx, cand in enumerate(candidates):
                if len(cand) > 1 and cand[1:] == target_trim:
                    return idx
            return None

        def trim_last_match(target, candidates):
            """删尾字符匹配"""
            if len(target) <= 1:
                return None
            target_trim = target[:-1]
            for idx, cand in enumerate(candidates):
                if len(cand) > 1 and cand[:-1] == target_trim:
                    return idx
            return None

        def match_server_name(target, candidates):
            """服务器名匹配：完全匹配 → 删首 → 删尾 → 模糊匹配"""
            # 1. 完全匹配
            idx = exact_match(target, candidates)
            if idx is not None:
                return idx
            # 2. 删首匹配
            idx = trim_first_match(target, candidates)
            if idx is not None:
                return idx
            # 3. 删尾匹配
            idx = trim_last_match(target, candidates)
            if idx is not None:
                return idx
            # 4. 字符集交集模糊匹配（阈值0.5）
            idx = fuzzy_match(target, candidates, threshold=0.5)
            return idx
        
        def match_character_name(target, candidates):
            """角色名匹配：完全匹配 → 删首 → 删尾（无模糊匹配）"""
            idx = exact_match(target, candidates)
            if idx is not None:
                return idx
            idx = trim_first_match(target, candidates)
            if idx is not None:
                return idx
            idx = trim_last_match(target, candidates)
            return idx
        # ---------------------------------

        lastList = []
        while 1:
            self.screenshot()
            ocrRes = self.O_SA_SELECT_SVR_CHARACTER_LIST.detect_and_ocr(self.device.image)
            #raw_names = [item.ocr_text for item in ocrRes]
            #logger.info(f"OCR raw names: {raw_names}")
            # 去除等级数字和括号
            processed_names = [item.ocr_text.lstrip('1234567890 ([<>])【】（）《》') for item in ocrRes]
            #logger.info(f"Processed names: {processed_names}")
            ocrBoxes = [item.box for item in ocrRes]

            matched_idx = None
            is_svr_match = False

            # 第一步：尝试匹配服务器名（使用带模糊匹配的策略）
            if svrName:
                matched_idx = match_server_name(svrName, processed_names)
                if matched_idx is not None:
                    is_svr_match = True
                    logger.info(f"Server name matched: {svrName} -> {processed_names[matched_idx]}")

            # 第二步：如果服务器名未匹配，则尝试匹配角色名（使用精确策略）
            if matched_idx is None:
                matched_idx = match_character_name(characterName, processed_names)
                if matched_idx is not None:
                    logger.info(f"Character name matched: {characterName} -> {processed_names[matched_idx]}")

            if matched_idx is not None:
                # 构建临时点击规则
                from copy import deepcopy
                tmp = self.O_SA_SELECT_SVR_CHARACTER_LIST
                tmpClick = RuleClick(
                    roi_back=deepcopy(tmp.roi),
                    roi_front=[
                        tmp.roi[0] + ocrBoxes[matched_idx][0][0],
                        tmp.roi[1] + ocrBoxes[matched_idx][0][1],
                        ocrBoxes[matched_idx][1][0] - ocrBoxes[matched_idx][0][0],
                        ocrBoxes[matched_idx][2][1] - ocrBoxes[matched_idx][1][1]
                    ],
                    name="tmpClick"
                )
                # 根据匹配类型调整点击偏移
                if is_svr_match:
                    tmpClick.roi_front[1] += 30   # 服务器名下方30px
                    logger.info("Click below server name (offset +30)")
                else:
                    tmpClick.roi_front[1] -= 30   # 角色名上方30px
                    logger.info("Click above character name (offset -30)")

                # 点击偏移后的点直到选择服务器区域消失（表示选中成功）
                self.ui_click_until_disappear(tmpClick, stop=self.I_SA_CHECK_SELECT_SVR_3, interval=3)
                logger.info("Target found and clicked svr icon")
                return True

            # 未匹配，滑动列表继续
            if lastList == processed_names:
                break
            lastList = processed_names
            self.swipe(self.S_SA_SVR_SWIPE_LEFT)
            time.sleep(1.5)

        # 全部滑动完毕仍未找到
        self.click(self.C_SA_LOGIN_FORM_CANCEL_SVR_SELECT, 1.5)
        return False

    def jump2SelectAccount(self):
        """
            跳转到切换账号页面 该页面有红色登录按钮
        @return:
        @rtype:
        """
        while 1:
            if self.appear(self.I_SA_NETEASE_GAME_LOGO) and self.appear(self.I_SA_ACCOUNT_LOGIN_BTN):
                return
            if self.appear_then_click(self.I_SA_SWITCH_ACCOUNT_BTN, interval=1.5):
                continue
            if self.appear(self.I_CHECK_LOGIN_FORM):
                self.click(self.C_SA_LOGIN_FORM_USER_CENTER, 1.5)
                continue
        return

    def selectAccount(self, accountInfo: AccountInfo):
        logger.info("start selectAccount")
        self.O_SA_ACCOUNT_ACCOUNT_LIST.keyword = accountInfo.account
        self.O_SA_ACCOUNT_ACCOUNT_SELECTED.keyword = accountInfo.account
        # 每次调用仅完整扫描一次账号列表；重新打开页面后的重试由 login() 负责。
        while 1:
            self.screenshot()
            if self.appear(self.I_SA_ACCOUNT_DROP_DOWN_CLOSED):
                if self.ocr_appear(self.O_SA_ACCOUNT_ACCOUNT_SELECTED):
                    return True
                self.ui_click_until_disappear(self.I_SA_ACCOUNT_DROP_DOWN_CLOSED,
                                              interval=1.5)
                continue

            # 账号列表已打开状态
            ocrRes = self.O_SA_ACCOUNT_ACCOUNT_LIST.detect_and_ocr(self.device.image)

            # 找到该账号
            for index, ocr_account in enumerate([ocrResItem.ocr_text for ocrResItem in ocrRes]):
                if not accountInfo.is_account_alias(ocr_account):
                    continue
                    # if accountInfo.account in [ocrResItem.ocr_text for ocrResItem in ocrRes]:
                    #     index = [ocrResItem.ocr_text for ocrResItem in ocrRes].index(accountInfo.account)
                ocrResBoxList = [ocrResItem.box for ocrResItem in ocrRes]
                self.O_SA_ACCOUNT_ACCOUNT_LIST.area = [
                    self.O_SA_ACCOUNT_ACCOUNT_LIST.roi[0] + ocrResBoxList[index][0][0],
                    self.O_SA_ACCOUNT_ACCOUNT_LIST.roi[1] + ocrResBoxList[index][0][1],
                    ocrResBoxList[index][1][0] - ocrResBoxList[index][0][0],
                    ocrResBoxList[index][2][1] - ocrResBoxList[index][1][1]]
                time.sleep(1)
                self.click(self.O_SA_ACCOUNT_ACCOUNT_LIST)
                logger.info("account [ %s ] found", accountInfo.account)
                return True

            # 未找到该账号
            if self.appear(self.I_SA_ACCOUNT_DROP_DOWN_ADD_ACCOUNT):
                break
            self.swipe(self.S_SA_ACCOUNT_LIST_UP, 1.5)
            time.sleep(0.5)
        logger.info("account [ %s ] not found ", accountInfo.account)
        return False

    # def loginSubmit(self, appleOrAndroid: bool):
    #     """
    #
    #     @param appleOrAndroid: 安卓平台还是苹果平台
    #     @type appleOrAndroid:   False           Apple
    #                             True            Android
    #     @return:
    #     @rtype:
    #     """
    #     self.screenshot()
    #     if not (self.appear(self.I_SA_ACCOUNT_LOGIN_BTN) and self.appear(self.I_SA_NETEASE_GAME_LOGO)):
    #         # 不在登录界面,返回失败
    #         return False
    #     self.ui_click(self.C_SA_LOGIN_FORM_LOGIN_BTN, self.I_SA_LOGIN_FORM_APPLE, 1)
    #     if appleOrAndroid:
    #         logger.info("APPLE selected")
    #         self.ui_click_until_disappear(self.I_SA_LOGIN_FORM_APPLE, 1)
    #     else:
    #         logger.info("ANDROID selected")
    #         self.ui_click_until_disappear(self.I_SA_LOGIN_FORM_ANDROID, 1)
    #     return True

    def login(self, accountInfo: AccountInfo) -> bool:
        """

        @param accountInfo:
        @type accountInfo:
        @return:    True    点击了"进入游戏"按钮
                    False   未找到相应角色
        @rtype:bool
        """
        self.screenshot()
        #
        if not (self.appear(self.I_CHECK_LOGIN_FORM) or self.appear(self.I_SA_NETEASE_GAME_LOGO)):
            logger.error("Unknown Page,%s %s Login Failed", accountInfo.character, accountInfo.svr)
            return False

        #
        isAccountLogon = False
        isCharacterSelected = False
        self.O_SA_ACCOUNT_ACCOUNT_SELECTED.keyword = accountInfo.account
        self.O_SA_LOGIN_FORM_USER_CENTER_ACCOUNT.keyword = accountInfo.account
        while 1:
            self.screenshot()
            # 处于 选择服务器界面 直接点击空白区域退出该界面 进入切换账号流程
            if self.appear(self.I_SA_CHECK_SELECT_SVR_4) or self.appear(self.I_SA_CHECK_SELECT_SVR_3):
                self.click(self.C_SA_LOGIN_FORM_CANCEL_SVR_SELECT)
                time.sleep(1)  # 等待界面关闭
                continue

            # 处于选择 苹果安卓界面
            if self.appear(self.I_SA_LOGIN_FORM_APPLE):
                btn = self.I_SA_LOGIN_FORM_ANDROID if accountInfo.apple_or_android else self.I_SA_LOGIN_FORM_APPLE
                self.ui_click_until_disappear(btn)
                time.sleep(2)  # 等待平台选择生效
                isAccountLogon = True
                continue

            # 处于选择账号界面
            if self.appear(self.I_SA_NETEASE_GAME_LOGO) and not self.appear(self.I_SA_LOGIN_FORM_APPLE):
                # 如果账号已登录，不再处理账号选择（避免重复执行 selectAccount）
                if isAccountLogon:
                    time.sleep(0.5)  # 稍作等待，让界面稳定
                    continue
                if not accountInfo.account:
                    logger.error("param account is None,cannot switch account")
                    return False
                # 当前选择账号不是account
                if not self.ocr_appear(self.O_SA_ACCOUNT_ACCOUNT_SELECTED):
                    # 没有找到account
                    MAX_RETRY = 3
                    found = False

                    for retry in range(MAX_RETRY):
                        logger.info("selectAccount attempt %d/%d", retry + 1, MAX_RETRY)

                        # 每一轮扫描账号列表都重置连续操作记录，避免上一轮的上滑次数使本轮无法滑到底部。
                        self.device.click_record_clear()

                        # 重置 OCR 区域为默认值，避免上次残留
                        self.O_SA_ACCOUNT_ACCOUNT_LIST.area = self.O_SA_ACCOUNT_ACCOUNT_LIST.roi
                        self.screenshot()

                        if self.selectAccount(accountInfo):
                            found = True
                            break


                        if retry < MAX_RETRY - 1:
                            # 切号重试：关闭账号选择页后重新打开。
                            logger.info("Account not found, retrying (close & reopen)")
                            self.ui_click_until_disappear(
                                self.C_SA_LOGIN_FORM_ACCOUNT_CLOSE_BTN,
                                stop=self.I_SA_NETEASE_GAME_LOGO,
                                interval=1.5,
                            )
                            time.sleep(1)
                            #self.jump2SelectAccount()
                            self.ui_click(self.C_SA_LOGIN_FORM_USER_CENTER,self.I_SA_NETEASE_GAME_LOGO,interval=1.5)
                            time.sleep(1)

                    if not found:
                        logger.error("selectAccount failed after %d retries", MAX_RETRY)
                        return False
                    # selectAccount 后更新图片
                    self.screenshot()
                    time.sleep(1)  # 等待账号选中状态稳定
                self.ui_click(self.I_SA_ACCOUNT_LOGIN_BTN, stop=self.I_SA_LOGIN_FORM_APPLE, interval=1)
                time.sleep(1.5)  # 等待登录弹窗出现
                continue

            # 在用户中心界面
            if self.appear(self.I_SA_SWITCH_ACCOUNT_BTN):
                # 如果当前已登录用户就是account
                ocrRes = self.O_SA_LOGIN_FORM_USER_CENTER_ACCOUNT.ocr_single(self.device.image)
                # NOTE 由于邮箱账号@符号极易被误识别为其他,故对账号信息做预处理 便于比对
                if (accountInfo.account is None) or accountInfo.account == "" or accountInfo.is_account_alias(ocrRes):
                    logger.info("current is the account we want:ocr result %s", ocrRes)
                    isAccountLogon = True
                    self.ui_click_until_disappear(self.C_SA_LOGIN_FORM_USER_CENTER_CLOSE_BTN, interval=1,
                                                  stop=self.I_SA_SWITCH_ACCOUNT_BTN)
                    time.sleep(1)  # 等待用户中心关闭
                    continue
                #
                if self.ui_click(self.I_SA_SWITCH_ACCOUNT_BTN, self.I_SA_NETEASE_GAME_LOGO):
                    isAccountLogon = False
                    time.sleep(1)  # 等待切换账号界面出现
                    continue
                continue

            # 在游戏登录界面 不在用户中心 不在切换账号界面
            if not (self.appear(self.I_SA_NETEASE_GAME_LOGO) or self.appear(self.I_SA_SWITCH_ACCOUNT_BTN)):
                # 判断是否已经账号登录
                if not isAccountLogon:
                    self.click(self.C_SA_LOGIN_FORM_USER_CENTER)
                    time.sleep(1)  # 等待用户中心弹出
                    continue

                # 已登录 调用统一的选择方法，传入角色名和服务器名 查找对应角色和服务器区名
                if not isCharacterSelected and self.select_target(accountInfo.character, accountInfo.svr):
                    isCharacterSelected = True
                    time.sleep(1)  # 等待角色选中生效
                    continue
                break
            continue

        if isAccountLogon and isCharacterSelected:
            # 成功登录账号 找到角色
            # self.ui_click_until_disappear(self.C_SA_LOGIN_FORM_ENTER_GAME_BTN, stop=self.I_CHECK_LOGIN_FORM)
            logger.info("character %s-%s account:%s %s login Success", accountInfo.character, accountInfo.svr,
                        accountInfo.account,
                        'Android' if accountInfo.apple_or_android else 'Apple')
            return True

        logger.error("character %s-%s account:%s %s login Failed", accountInfo.character, accountInfo.svr,
                     accountInfo.account,
                     'Android' if accountInfo.apple_or_android else 'Apple')
        return False

    def ui_click_until_disappear(self, click, interval: float = 1, stop: RuleImage | RuleGif = None):
        """
        重写原ui_click_until_disappear方法,增加stop参数
        点击一个按钮直到stop消失
        如果click为RuleOcr ,直接当作RuleClick点击,不会进行ocr识别,
        @param interval:
        @param click:
        @param stop:
        @type stop:
        @return:
        """
        if (isinstance(click, RuleImage) or isinstance(click, RuleGif)) and (stop is None):
            stop = click
        while 1:
            self.screenshot()
            if not self.appear(stop):
                break
            if isinstance(click, RuleImage) or isinstance(click, RuleGif):
                self.appear_then_click(click, interval=interval)
                continue
            elif isinstance(click, RuleClick):
                self.click(click, interval)
                continue
            elif isinstance(click, RuleOcr):
                self.click(click)
                continue
