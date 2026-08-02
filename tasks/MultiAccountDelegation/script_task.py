import importlib
from datetime import datetime, timedelta
from pathlib import Path
from time import sleep

from module.base.timer import Timer
from module.exception import RequestHumanTakeover, TaskEnd
from module.logger import logger
from tasks.Component.SwitchAccount.switch_account import SwitchAccount
from tasks.Component.SwitchAccount.assets import SwitchAccountAssets
from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.page import page_delegation
from tasks.MultiAccountDelegation.assets import MultiAccountDelegationAssets
from tasks.MultiAccountDelegation.config import DelegationInterval, MultiAccountDelegation


class ScriptTask(GameUi, MultiAccountDelegationAssets, SwitchAccountAssets):
    """
    多账号式神委派任务的外层调度器。

    这个任务不直接复用原始的 Delegation 任务，而是把它的执行逻辑复制过来，改造成“按账号轮流执行”的模式：
    1. 先根据每个账号的下一次委派时间筛选出本轮需要执行的账号；
    2. 依次切换账号并执行委派流程；
    3. 记录每个账号下次委派时间，并让外层任务继续按最早到期的账号调度。
    """

    fade_conf: MultiAccountDelegation = None

    def run(self):
        self.fade_conf = self.config.multi_account_delegation
        now = datetime.now()

        current_account_info = getattr(self, 'current_account_info', None)
        matching_accounts = None
        if current_account_info is not None:
            matching_accounts = [
                account_info
                for account_info in self.fade_conf.account_list
                if self._account_matches_current(account_info, current_account_info)
            ]

        pending_accounts = []
        for account_info in self.fade_conf.account_list:
            if not account_info.is_valid():
                continue
            if matching_accounts is not None and matching_accounts and account_info not in matching_accounts:
                logger.info('skip account %s-%s because it does not match current account %s-%s', account_info.character, account_info.svr, getattr(current_account_info, 'character', ''), getattr(current_account_info, 'svr', ''))
                continue

            next_delegation_time = account_info.next_delegation_time
            if next_delegation_time and next_delegation_time > now:
                logger.info('%s-%s next delegation is %s, skip', account_info.character, account_info.svr, next_delegation_time)
                continue

            pending_accounts.append(account_info)

        if not pending_accounts:
            self.set_next_run('MultiAccountDelegation', target=self._get_next_run_time())
            raise TaskEnd('MultiAccountDelegation')

        for account_info in pending_accounts:
            logger.info('start account %s-%s', account_info.character, account_info.svr)

            suc = SwitchAccount(self.config, self.device, account_info).switchAccount()
            if not suc:
                logger.warning('switch to %s-%s failed', account_info.character, account_info.svr)
                continue

            try:
                self._run_delegation_for_account(account_info)
            except TaskEnd:
                logger.warning('%s-%s delegation task ended', account_info.character, account_info.svr)
            except RequestHumanTakeover:
                raise
            except Exception as e:
                logger.error('run delegation failed for %s-%s: %s', account_info.character, account_info.svr, e)
                self.set_next_run('MultiAccountDelegation', success=False)
                break

            next_delegation_time = self._calculate_next_delegation_time(
                self.fade_conf.multi_account_delegation_config.delegation_interval,
                datetime.now(),
            )
            self.fade_conf.update_account_next_delegation_time(account_info, next_delegation_time)
            self.fade_conf.update_account_login_history(account_info)
            self.config.model.multi_account_delegation = self.fade_conf
            self.config.save()

        next_run = self._get_next_run_time()
        self.set_next_run('MultiAccountDelegation', target=next_run)
        raise TaskEnd('MultiAccountDelegation')

    def _account_matches_current(self, account_info, current_account_info) -> bool:
        if current_account_info is None:
            return True

        current_character = getattr(current_account_info, 'character', '') or ''
        current_svr = getattr(current_account_info, 'svr', '') or ''
        current_account = getattr(current_account_info, 'account', '') or ''

        if current_account:
            account_value = getattr(account_info, 'account', '') or ''
            if account_value and account_value == current_account:
                return True

        if current_character and current_svr:
            return account_info.character == current_character and account_info.svr == current_svr

        if current_character:
            return account_info.character == current_character

        return False

    def _run_delegation_for_account(self, account_info):
        """
        为当前账号执行一轮式神委派。

        这里把原始 Delegation 任务的逻辑复制过来，并改成从当前账号的配置中读取委派开关。
        """
        self.goto_page(page_delegation)
        self.check_reward()

        if account_info.miyoshino_painting:
            self.delegate_one('画')
        if account_info.bird_feather:
            self.delegate_one('鸟羽')
        if account_info.find_earring:
            self.delegate_one('寻找耳环')
        if account_info.cat_boss:
            self.delegate_one('猫老大')
        if account_info.miyoshino:
            self.delegate_one('接送')
        if account_info.strange_trace:
            self.delegate_one('痕迹')

    def delegate_one(self, name: str) -> bool:
        def ui_click(click, stop):
            while True:
                self.screenshot()
                if self.appear(stop):
                    break
                if self.click(click, interval=1.5):
                    continue

        logger.hr('Delegation one', 2)
        self.O_D_NAME.keyword = name
        self.screenshot()
        if not self.ocr_appear(self.O_D_NAME):
            logger.warning('Delegation: %s not found', name)
            return False

        while True:
            self.screenshot()
            if self.appear(self.I_D_START):
                break
            if self.appear(self.I_D_BACK):
                logger.warning('Delegation: %s is in delegation', name)
                self.ui_click_until_disappear(self.I_D_BACK)
                self.wait_until_appear(self.I_REWARDS_MIN)
                return False
            if self.appear_then_click(self.I_D_SKIP, interval=0.8):
                continue
            if self.appear_then_click(self.I_D_CONFIRM, interval=0.8):
                continue
            if self.ocr_appear_click(self.O_D_NAME, interval=1):
                continue

        logger.info('Enter Delegation: %s', name)
        ui_click(self.C_D_1, self.I_D_SELECT_1)
        ui_click(self.C_D_2, self.I_D_SELECT_2)
        ui_click(self.C_D_3, self.I_D_SELECT_3)
        ui_click(self.C_D_4, self.I_D_SELECT_4)

        logger.info('Delegation: %s start', name)
        while True:
            self.screenshot()
            if not self.appear(self.I_D_START):
                break
            if self.click(self.C_D_5, interval=0.8):
                continue
            if self.appear_then_click(self.I_D_START, interval=1.8):
                continue

        return True

    def check_reward(self):
        check_timer = Timer(3)
        check_timer.start()
        while True:
            self.screenshot()
            if self.appear_then_click(self.I_REWARDS_GET, interval=1):
                check_timer.reset()
                continue
            if self.appear_then_click(self.I_REWARDS_CHAT, interval=1):
                check_timer.reset()
                continue
            if self.appear_then_click(self.I_CHAT_1, interval=1):
                check_timer.reset()
                continue
            if self.appear_then_click(self.I_CHAT_2, interval=1):
                check_timer.reset()
                continue
            if self.appear_then_click(self.I_REWARDS_DONE, interval=1):
                check_timer.reset()
                continue
            if self.appear_then_click(self.I_REWARDS_FALSE, interval=1):
                check_timer.reset()
                continue

            if not self.appear(self.I_REWARDS_MIN):
                continue
            if check_timer.reached():
                break
            if self.ocr_appear_click(self.O_D_DONE, interval=1):
                check_timer.reset()
                continue

    def _calculate_next_delegation_time(self, interval: DelegationInterval, now: datetime) -> datetime:
        if interval == DelegationInterval.SIX_HOURS:
            return now + timedelta(hours=6)

        # 完成时间循环暂时先使用一个稳定的默认间隔，后续可再细化成更具体的策略。
        return now + timedelta(hours=6)

    def _get_next_run_time(self) -> datetime:
        next_times = [
            account.next_delegation_time
            for account in self.fade_conf.account_list
            if account.is_valid() and account.next_delegation_time
        ]
        if not next_times:
            return datetime.now() + timedelta(hours=6)
        return min(next_times)
