import copy
from datetime import datetime, timedelta

from module.logger import logger
from tasks.Component.MultiAccount.multi_account_task import MultiAccountTaskBase
from tasks.KekkaiUtilize.config import UtilizeConfig
from tasks.MultiAccountKekkaiUtilize.config import MultiAccountKekkaiUtilize


class ScriptTask(MultiAccountTaskBase):
    """多账号结界蹭卡任务。"""

    task_name = "MultiAccountKekkaiUtilize"
    priority_config_attr = "multi_account_kekkai_count_config"
    multi_account_config_attr = "multi_account_kekkai_utilize"
    inner_task_display_name = "结界蹭卡"
    fade_conf: MultiAccountKekkaiUtilize = None
    retry_delay = timedelta(minutes=10)
    # 蹭卡按每个账号的 next_utilize_time 调度，不能被服务器更新时间覆盖。
    server_schedule = False

    def collect_pending_accounts(self, now: datetime) -> list[tuple[int, object]]:
        """筛选到达执行时间且不处于禁止时段的账号。"""
        pending_accounts = []
        for index, account_info in enumerate(self.fade_conf.account_list):
            # 公共账号标识无效时不能继续使用账号组中残留的旧登录信息。
            if id(account_info) in getattr(self, "_invalid_shared_account_ids", set()):
                continue
            if not account_info.is_valid():
                continue
            if not self._is_account_in_scope(account_info):
                continue

            next_utilize_time = account_info.next_utilize_time
            # 提前修正落在禁止时段内的计划时间，避免无意义地唤醒模拟器。
            deferred_time = self._get_deferred_forbidden_time(
                index,
                account_info,
                next_utilize_time,
                "下一次蹭卡时间",
            )
            if deferred_time is not None:
                account_info.next_utilize_time = deferred_time
                continue

            if next_utilize_time and next_utilize_time > now:
                logger.info(
                    "%s-%s 下一次蹭卡时间为 %s，本轮跳过",
                    account_info.character,
                    account_info.svr,
                    next_utilize_time,
                )
                continue

            # 计划时间可能因排队、重启或模拟器启动变慢而延后，必须复核实际执行时间。
            deferred_time = self._get_deferred_forbidden_time(
                index,
                account_info,
                now,
                "当前执行时间",
            )
            if deferred_time is not None:
                account_info.next_utilize_time = deferred_time
                continue

            pending_accounts.append((index, account_info))
        return pending_accounts

    def get_account_config(self, index: int, account_info: object):
        """按账号下标返回当前账号实际使用的蹭卡配置。"""
        return self._get_active_utilize_config(index)

    def prepare_account(self, index: int, account_info: object) -> None:
        """把当前账号蹭卡配置临时覆盖到内层任务。"""
        self._apply_account_utilize_config(index)
        self._current_next_utilize_time = None

    def run_account(self, index: int, account_info: object) -> bool | None:
        """调用原有结界蹭卡任务执行真实业务。"""
        task_obj = self.create_task_object(
            "KekkaiUtilize",
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
        """读取内层调度结果后恢复配置，避免污染下一个账号。"""
        try:
            if account_success:
                next_utilize_time = self._get_kekkai_utilize_next_run_time()
                self._current_next_utilize_time = next_utilize_time
        finally:
            self._restore_utilize_config()

    def on_account_success(self, index: int, account_info: object) -> bool | None:
        """保存当前账号下一次蹭卡时间。"""
        next_utilize_time = getattr(self, "_current_next_utilize_time", None)
        if next_utilize_time is None:
            logger.warning(
                "%s-%s 未生成下一次蹭卡时间",
                account_info.character,
                account_info.svr,
            )
            return False

        # 内层任务生成下次时间后立即避开禁止时段，避免到点后再空跑一次任务。
        deferred_time = self._get_deferred_forbidden_time(
            index,
            account_info,
            next_utilize_time,
            "下一次蹭卡时间",
        )
        account_info.next_utilize_time = deferred_time or next_utilize_time
        account_info.last_complete_time = datetime.now()
        return True

    def on_account_failure(self, index: int, account_info: object, reason: str) -> None:
        """失败账号延后重试，避免旧时间过期导致立即死循环。"""
        account_info.next_utilize_time = self._get_retry_time()
        logger.warning(
            "%s-%s 本轮处理失败（%s），安排在 %s 后重试",
            account_info.character,
            account_info.svr,
            reason,
            account_info.next_utilize_time,
        )

    def _get_active_utilize_config(self, index: int):
        """按账号下标选择私有蹭卡配置或公共配置。"""
        account_info = self.fade_conf.account_list[index]
        if (
            account_info.enable_private_config
            and index < len(self.fade_conf.private_config)
        ):
            return self.fade_conf.private_config[index]
        return self.fade_conf.multi_account_kekkai_utilize_config

    def _get_active_forbid_config(self, index: int):
        """按账号下标获取私有禁止时段配置。"""
        if index < len(self.fade_conf.private_forbid_config):
            return self.fade_conf.private_forbid_config[index]
        return None

    def _apply_account_utilize_config(self, index: int):
        """把当前账号配置临时覆盖到内层蹭卡任务。"""
        self._utilize_config_backup = copy.deepcopy(self.config.kekkai_utilize.utilize_config)
        self._kekkai_scheduler_backup = copy.deepcopy(self.config.kekkai_utilize.scheduler)
        self._config_save_fields_backup = getattr(
            self.config,
            "_save_selected_fields",
            None,
        )
        # 内层任务设置下次运行时间时，只合并写回结界蹭卡配置。
        self.config._save_selected_fields = {"kekkai_utilize"}

        selected_config = self._get_active_utilize_config(index)
        self.config.kekkai_utilize.utilize_config = UtilizeConfig(
            **selected_config.model_dump()
        )

    def _restore_utilize_config(self):
        """恢复内层蹭卡配置和调度器，防止账号之间相互污染。"""
        if not hasattr(self, "_utilize_config_backup"):
            return

        self.config.kekkai_utilize.utilize_config = copy.deepcopy(
            self._utilize_config_backup
        )
        self.config.kekkai_utilize.scheduler = copy.deepcopy(self._kekkai_scheduler_backup)
        del self._utilize_config_backup
        del self._kekkai_scheduler_backup
        self.config._save_selected_fields = getattr(
            self,
            "_config_save_fields_backup",
            None,
        )
        if hasattr(self, "_config_save_fields_backup"):
            del self._config_save_fields_backup

    def _get_active_forbid_windows(self, index: int):
        """获取当前账号实际使用的禁止蹭卡时段列表，私有配置优先于公共配置。"""
        account_info = self.fade_conf.account_list[index]
        private_config = self._get_active_forbid_config(index)
        if account_info.enable_private_forbid_time and private_config:
            return private_config.get_forbid_windows()

        public_config = self.fade_conf.multi_account_kekkai_forbid_config
        if public_config.public_forbid_time_enable:
            return public_config.get_forbid_windows()

        return []

    def _get_deferred_forbidden_time(
        self,
        index: int,
        account_info: object,
        reference_time: datetime | None,
        reference_name: str,
    ) -> datetime | None:
        """若指定时间处于禁止时段，返回顺延后的时间；否则返回 None。"""
        if not self._is_in_forbidden_period(index, reference_time):
            return None

        next_time = self._get_end_forbidden_time(index, reference_time)
        logger.info(
            "%s-%s 的%s %s 位于禁止蹭卡时段，调整为 %s",
            account_info.character,
            account_info.svr,
            reference_name,
            reference_time,
            next_time,
        )
        return next_time

    def _is_in_forbidden_period(self, index: int, next_utilize_time):
        if not next_utilize_time:
            return False

        windows = self._get_active_forbid_windows(index)
        if not windows:
            return False

        current_time = next_utilize_time.time()
        for start, end in windows:
            if start == end:
                continue
            if start < end:
                if start <= current_time < end:
                    return True
            elif current_time >= start or current_time < end:
                return True
        return False

    def _get_end_forbidden_time(self, index: int, next_utilize_time):
        """计算当前禁止时段合并后的结束时间。"""
        windows = self._get_active_forbid_windows(index)
        if not windows:
            return next_utilize_time

        current = next_utilize_time
        intervals = []
        for day_offset in (-1, 0, 1):
            current_date = current.date() + timedelta(days=day_offset)
            for start, end in windows:
                start_at = datetime.combine(current_date, start)
                end_at = datetime.combine(current_date, end)
                if end <= start:
                    end_at += timedelta(days=1)
                intervals.append((start_at, end_at))

        active_intervals = [
            (start_at, end_at)
            for start_at, end_at in intervals
            if start_at <= current < end_at
        ]
        if not active_intervals:
            return current + timedelta(minutes=10)

        forbidden_end = max(end_at for _, end_at in active_intervals)
        while True:
            extended_end = max(
                (
                    end_at
                    for start_at, end_at in intervals
                    if start_at <= forbidden_end
                ),
                default=forbidden_end,
            )
            if extended_end <= forbidden_end:
                break
            forbidden_end = extended_end

        return forbidden_end + timedelta(minutes=10)

    def _get_retry_time(self):
        return datetime.now() + self.retry_delay

    def get_next_run_time(self):
        return self._get_next_run_time()

    def _get_next_run_time(self):
        next_times = [
            account.next_utilize_time
            for account in self.get_scoped_accounts()
            if id(account) not in getattr(self, "_invalid_shared_account_ids", set())
            and account.is_valid() and account.next_utilize_time
        ]
        if not next_times:
            return datetime.now() + timedelta(hours=1)
        return min(next_times)

    def _get_kekkai_utilize_next_run_time(self):
        scheduler = getattr(self.config.kekkai_utilize, "scheduler", None)
        next_run = getattr(scheduler, "next_run", None) if scheduler else None
        return next_run if isinstance(next_run, datetime) else None

    def _save_fade_config(self):
        """兼容旧调用方的保存方法。"""
        self.save_multi_account_config()
