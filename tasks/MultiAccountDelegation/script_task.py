import copy
from datetime import datetime, timedelta

from module.exception import RequestHumanTakeover
from module.logger import logger
from tasks.Component.MultiAccount.multi_account_task import MultiAccountTaskBase
from tasks.Component.SwitchAccount.switch_account import SwitchAccount
from tasks.Delegation.config import DelegationConfig
from tasks.MultiAccountDelegation.assets import MultiAccountDelegationAssets
from tasks.MultiAccountDelegation.config import (
    DelegationInterval,
    MultiAccountDelegation,
    MultiAccountDelegationAccount,
)


class ScriptTask(MultiAccountTaskBase, MultiAccountDelegationAssets):
    """多账号式神委派任务。"""

    task_name = "MultiAccountDelegation"
    multi_account_config_attr = "multi_account_delegation"
    server_schedule = False
    retry_delay = timedelta(minutes=10)
    fade_conf: MultiAccountDelegation = None

    def collect_pending_accounts(self, now: datetime) -> list[tuple[int, object]]:
        """筛选已经到达下一次委派时间的账号。"""
        pending_accounts = []
        for index, account_info in enumerate(self.fade_conf.account_list):
            if not account_info.is_valid():
                continue
            if not self._is_account_in_scope(account_info):
                continue
            next_delegation_time = account_info.next_delegation_time
            if next_delegation_time and next_delegation_time > now:
                logger.info(
                    "%s-%s 下一次委派时间为 %s，本轮跳过",
                    account_info.character,
                    account_info.svr,
                    next_delegation_time,
                )
                continue
            pending_accounts.append((index, account_info))
        return pending_accounts

    def get_account_config(self, index: int, account_info: object):
        """按账号下标选择私有式神委派配置或公共配置。"""
        if (
            account_info.enable_private_config
            and index < len(self.fade_conf.private_config)
        ):
            return self.fade_conf.private_config[index]
        return self.fade_conf.multi_account_delegation_config

    def prepare_account(self, index: int, account_info: object) -> None:
        """备份内层配置，并应用当前账号的委派开关。"""
        self._delegation_config_backup = copy.deepcopy(
            self.config.delegation.delegation_config
        )
        self._delegation_scheduler_backup = copy.deepcopy(
            self.config.delegation.scheduler
        )
        selected_config = self.get_account_config(index, account_info)
        self.config.delegation.delegation_config = DelegationConfig(
            **selected_config.model_dump()
        )
        # 内层任务设置下次调度时会重新加载配置，因此必须先保存临时账号配置。
        self.config.save()

    def run_account(self, index: int, account_info: object) -> bool | None:
        """调用原有式神委派任务执行真实委派动作。"""
        self._run_delegation_for_account(account_info)
        return True

    def cleanup_account(
        self,
        index: int,
        account_info: object,
        account_success: bool,
    ) -> None:
        """恢复内层配置，避免账号之间互相污染。"""
        self._restore_delegation_config()

    def on_account_success(self, index: int, account_info: object) -> bool | None:
        """保存当前账号下一次委派时间。"""
        interval = self.fade_conf.multi_account_delegation_count_config.delegation_interval
        account_info.next_delegation_time = self._calculate_next_delegation_time(
            interval,
            datetime.now(),
        )
        account_info.last_complete_time = datetime.now()
        return True

    def on_account_failure(self, index: int, account_info: object, reason: str) -> None:
        """失败账号延后重试，避免旧时间导致立即重复执行。"""
        account_info.next_delegation_time = datetime.now() + self.retry_delay
        logger.warning(
            "%s-%s 本轮委派失败（%s），安排在 %s 后重试",
            account_info.character,
            account_info.svr,
            reason,
            account_info.next_delegation_time,
        )

    def _run_delegation_for_account(
        self,
        account_info: MultiAccountDelegationAccount,
    ) -> None:
        """加载原有式神委派任务，执行当前账号的委派流程。"""
        task_obj = self.create_task_object(
            "Delegation",
            config=self.config,
            device=self.device,
        )
        task_obj.run()

    def _restore_delegation_config(self) -> None:
        """恢复内层式神委派配置和调度器。"""
        if not hasattr(self, "_delegation_config_backup"):
            return

        self.config.model.delegation.delegation_config = copy.deepcopy(
            self._delegation_config_backup
        )
        self.config.model.delegation.scheduler = copy.deepcopy(
            self._delegation_scheduler_backup
        )
        del self._delegation_config_backup
        del self._delegation_scheduler_backup

    @staticmethod
    def _calculate_next_delegation_time(
        interval: DelegationInterval,
        now: datetime,
    ) -> datetime:
        """计算账号下一次委派时间。"""
        # 原有逻辑中两种模式都按六小时轮询，保留现有行为。
        if interval in {
            DelegationInterval.SIX_HOURS,
            DelegationInterval.COMPLETION_TIME,
        }:
            return now + timedelta(hours=6)
        return now + timedelta(hours=6)

    def get_next_run_time(self) -> datetime:
        return self._get_next_run_time()

    def _get_next_run_time(self) -> datetime:
        """返回所有有效账号中最早的下一次委派时间。"""
        next_times = [
            account.next_delegation_time
            for account in self.get_scoped_accounts()
            if account.is_valid() and account.next_delegation_time
        ]
        if not next_times:
            return datetime.now() + timedelta(hours=6)
        return min(next_times)

    @staticmethod
    def _account_matches_current(account_info, current_account_info) -> bool:
        """兼容被其他任务指定当前账号时的单账号执行模式。"""
        current_character = getattr(current_account_info, "character", "") or ""
        current_svr = getattr(current_account_info, "svr", "") or ""
        current_account = getattr(current_account_info, "account", "") or ""

        if current_account:
            account_value = getattr(account_info, "account", "") or ""
            if account_value and account_value == current_account:
                return True

        if current_character and current_svr:
            return (
                account_info.character == current_character
                and account_info.svr == current_svr
            )
        if current_character:
            return account_info.character == current_character
        return False

    def _switch_account(self, account: object) -> bool:
        """切换账号，保留旧接口并统一转换切号异常。"""
        try:
            return SwitchAccount(self.config, self.device, account).switchAccount()
        except RequestHumanTakeover:
            raise
        except Exception as exc:
            logger.exception(
                "切换账号时发生异常（%s-%s）：%s",
                account.character,
                account.svr,
                exc,
            )
            return False

    def _save_fade_config(self) -> None:
        """兼容旧调用方的保存方法。"""
        self.save_multi_account_config()
