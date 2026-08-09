import copy
from datetime import datetime, timedelta

from module.logger import logger
from tasks.Component.MultiAccount.multi_account_task import MultiAccountTaskBase
from tasks.MultiAccountKekkaiActivation.assets import MultiAccountKekkaiActivationAssets
from tasks.MultiAccountKekkaiActivation.config import (
    MultiAccountKekkaiActivation,
    MultiAccountKekkaiActivationAccount,
)


class ScriptTask(MultiAccountTaskBase, MultiAccountKekkaiActivationAssets):
    """多账号结界挂卡任务。"""

    task_name = "MultiAccountKekkaiActivation"
    multi_account_config_attr = "multi_account_kekkai_activation"
    server_schedule = False
    retry_delay = timedelta(minutes=10)
    fade_conf: MultiAccountKekkaiActivation = None

    def collect_pending_accounts(self, now: datetime) -> list[tuple[int, object]]:
        """筛选已经到达下一次挂卡时间的账号。"""
        pending_accounts = []
        for index, account_info in enumerate(self.fade_conf.account_list):
            if not account_info.is_valid():
                continue
            if not self._is_account_in_scope(account_info):
                continue

            next_activation_time = account_info.next_activation_time
            if next_activation_time and next_activation_time > now:
                logger.info(
                    "%s-%s 下一次挂卡时间为 %s，本轮跳过",
                    account_info.character,
                    account_info.svr,
                    next_activation_time,
                )
                continue
            pending_accounts.append((index, account_info))
        return pending_accounts

    def get_account_config(self, index: int, account_info: object):
        """按账号下标选择私有挂卡配置或公共挂卡配置。"""
        if (
            account_info.enable_private_config
            and index < len(self.fade_conf.private_config)
        ):
            return self.fade_conf.private_config[index]
        return self.fade_conf.multi_account_kekkai_activation_config

    def prepare_account(self, index: int, account_info: object) -> None:
        """备份内层配置，并应用当前账号的挂卡配置。"""
        self._activation_config_backup = copy.deepcopy(
            self.config.kekkai_activation.activation_config
        )
        self._kekkai_activation_scheduler_backup = copy.deepcopy(
            self.config.kekkai_activation.scheduler
        )
        self._current_next_activation_time = None
        self._apply_account_activation_config(index, account_info)
        # 内层任务设置下次调度时会重新加载配置，因此必须先保存临时账号配置。
        self.config.save()

    def run_account(self, index: int, account_info: object) -> bool | None:
        """调用原有结界挂卡任务执行真实挂卡动作。"""
        task_obj = self.create_task_object(
            "KekkaiActivation",
            config=self.config,
            device=self.device,
        )
        task_obj.run()
        return True

    def cleanup_account(
        self,
        index: int,
        account_info: object,
        account_success: bool,
    ) -> None:
        """同步账号状态、读取下次时间并恢复内层配置。"""
        try:
            self._sync_account_activation_state(account_info, index=index)
            if account_success:
                self._current_next_activation_time = (
                    self._get_kekkai_activation_next_run_time()
                )
        finally:
            self._restore_activation_config()

    def on_account_success(self, index: int, account_info: object) -> bool | None:
        """保存内层挂卡任务生成的下一次运行时间。"""
        next_activation_time = getattr(
            self,
            "_current_next_activation_time",
            None,
        )
        if next_activation_time is None:
            logger.warning(
                "%s-%s 未生成下一次挂卡时间",
                account_info.character,
                account_info.svr,
            )
            return False

        account_info.next_activation_time = next_activation_time
        account_info.last_complete_time = datetime.now()
        return True

    def on_account_failure(self, index: int, account_info: object, reason: str) -> None:
        """失败账号延后重试，避免旧时间导致立即重复执行。"""
        account_info.next_activation_time = datetime.now() + self.retry_delay
        logger.warning(
            "%s-%s 本轮挂卡失败（%s），安排在 %s 后重试",
            account_info.character,
            account_info.svr,
            reason,
            account_info.next_activation_time,
        )

    def _apply_account_activation_config(
        self,
        index: int | None = None,
        account_info: MultiAccountKekkaiActivationAccount | None = None,
    ) -> None:
        """把公共或私有配置临时应用到内层挂卡任务。"""
        if account_info is None:
            account_info = self.current_account_info
        if index is None:
            index = self._resolve_account_index(account_info)
        selected_config = self.get_account_config(index, account_info)
        public_config = self.fade_conf.multi_account_kekkai_activation_config
        activation_config = self.config.kekkai_activation.activation_config

        activation_config.card_type = selected_config.card_type
        activation_config.card_star = selected_config.card_star
        activation_config.swipe_retry_limit = selected_config.swipe_retry_limit
        activation_config.min_taiko_num = selected_config.min_taiko_num
        activation_config.min_fish_num = selected_config.min_fish_num
        activation_config.auto_fill = selected_config.auto_fill
        activation_config.shikigami_class = selected_config.shikigami_class
        activation_config.exchange_before = public_config.exchange_before
        activation_config.exchange_max = public_config.exchange_max
        activation_config.card_not_found_count = account_info.card_not_found_count

    def _sync_account_activation_state(
        self,
        account_info: MultiAccountKekkaiActivationAccount,
        index: int | None = None,
    ) -> None:
        """同步内层任务产生的卡类型和未找到卡次数。"""
        if index is None:
            index = self._resolve_account_index(account_info)

        activation_config = self.config.kekkai_activation.activation_config
        account_info.card_not_found_count = activation_config.card_not_found_count
        selected_config = self.get_account_config(index, account_info)
        selected_config.card_type = activation_config.card_type
        account_info.card_type = activation_config.card_type

    def _restore_activation_config(self) -> None:
        """恢复内层挂卡配置和调度器，防止账号之间相互污染。"""
        if not hasattr(self, "_activation_config_backup"):
            return

        self.config.kekkai_activation.activation_config = copy.deepcopy(
            self._activation_config_backup
        )
        self.config.kekkai_activation.scheduler = copy.deepcopy(
            self._kekkai_activation_scheduler_backup
        )
        del self._activation_config_backup
        del self._kekkai_activation_scheduler_backup

    def get_next_run_time(self) -> datetime:
        return self._get_next_run_time()

    def _get_next_run_time(self) -> datetime:
        """返回所有有效账号中最早的下一次挂卡时间。"""
        next_times = [
            account.next_activation_time
            for account in self.get_scoped_accounts()
            if account.is_valid() and account.next_activation_time
        ]
        if not next_times:
            return datetime.now() + timedelta(hours=1)
        return min(next_times)

    def _get_kekkai_activation_next_run_time(self) -> datetime | None:
        """读取内层挂卡任务生成的下一次调度时间。"""
        scheduler = getattr(self.config.kekkai_activation, "scheduler", None)
        next_run = getattr(scheduler, "next_run", None) if scheduler else None
        return next_run if isinstance(next_run, datetime) else None

    def _resolve_account_index(
        self,
        account_info: MultiAccountKekkaiActivationAccount,
    ) -> int:
        """根据对象身份或账号信息查找账号下标。"""
        for index, account in enumerate(self.fade_conf.account_list):
            if account is account_info:
                return index
        for index, account in enumerate(self.fade_conf.account_list):
            if (
                account.character == account_info.character
                and account.svr == account_info.svr
            ):
                return index
        raise IndexError("无法找到当前账号对应的配置下标")

    def _save_fade_config(self) -> None:
        """兼容旧调用方的保存方法。"""
        self.save_multi_account_config()
