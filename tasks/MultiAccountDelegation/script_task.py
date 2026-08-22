import copy
from datetime import datetime, timedelta

from module.logger import logger
from tasks.Component.MultiAccount.multi_account_task import MultiAccountTaskBase
from tasks.Delegation.config import DelegationConfig
from tasks.MultiAccountDelegation.assets import MultiAccountDelegationAssets
from tasks.MultiAccountDelegation.config import (
    DelegationInterval,
    MultiAccountDelegation,
)


class ScriptTask(MultiAccountTaskBase, MultiAccountDelegationAssets):
    """多账号式神委派任务。"""

    task_name = "MultiAccountDelegation"
    priority_config_attr = "multi_account_delegation_count_config"
    multi_account_config_attr = "multi_account_delegation"
    inner_task_display_name = "式神委派"
    server_schedule = False
    retry_delay = timedelta(minutes=10)
    fade_conf: MultiAccountDelegation = None

    def collect_pending_accounts(self, now: datetime) -> list[tuple[int, object]]:
        """筛选已经到达下一次委派时间的账号。"""
        pending_accounts = []
        for index, account_info in enumerate(self.fade_conf.account_list):
            # 公共账号序号无效时不能继续使用账号组中残留的旧登录信息。
            if id(account_info) in getattr(self, "_invalid_shared_account_ids", set()):
                continue
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
        self._config_save_fields_backup = getattr(
            self.config,
            "_save_selected_fields",
            None,
        )
        # 内层任务设置下次运行时间时，只合并写回式神委派配置。
        self.config._save_selected_fields = {"delegation"}
        selected_config = self.get_account_config(index, account_info)
        self.config.delegation.delegation_config = DelegationConfig(
            **selected_config.model_dump()
        )

    def run_account(self, index: int, account_info: object) -> bool | None:
        """直接调用原式神委派任务，复用其全部逻辑和优化。"""
        task_obj = self.create_task_object(
            "Delegation",
            config=self.config,
            device=self.device,
        )
        # 内层原任务不应覆盖多账号式神委派的调度时间。
        task_obj.set_next_run = self._skip_inner_task_schedule
        task_obj.run()
        return True

    def _skip_inner_task_schedule(self, *args, **kwargs) -> None:
        """防止原式神委派覆盖多账号式神委派的调度时间。"""

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
        self.config._save_selected_fields = getattr(
            self,
            "_config_save_fields_backup",
            None,
        )
        if hasattr(self, "_config_save_fields_backup"):
            del self._config_save_fields_backup

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
            if id(account) not in getattr(self, "_invalid_shared_account_ids", set())
            and account.is_valid() and account.next_delegation_time
        ]
        if not next_times:
            return datetime.now() + timedelta(hours=6)
        return min(next_times)

    def _save_fade_config(self) -> None:
        """兼容旧调用方的保存方法。"""
        self.save_multi_account_config()
