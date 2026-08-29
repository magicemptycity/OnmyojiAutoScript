from __future__ import annotations

import copy
from datetime import datetime, timedelta
from typing import Any, ClassVar

from module.config.utils import convert_to_underscore
from module.exception import RequestHumanTakeover, TaskEnd
from module.logger import logger
from pydantic import ValidationError
from tasks.KekkaiUtilize.config import UtilizeConfig
from tasks.MultiAccountKekkaiUtilizeNew.config import (
    MultiAccountKekkaiUtilizeNew,
    MultiAccountKekkaiUtilizeNewAccount,
)
from tasks.MultiAccountRepeatNew.script_task import ScriptTask as MultiAccountRepeatNewBase
from tasks.Restart.server_update import build_server_update_delay_target, is_server_update_window


class ScriptTask(MultiAccountRepeatNewBase):
    """使用公共账号库、账号独立时间和禁止时段的多账号蹭卡新。"""

    task_name: ClassVar[str] = "MultiAccountKekkaiUtilizeNew"
    multi_account_config_attr: ClassVar[str] = "multi_account_kekkai_utilize_new"
    priority_config_attr: ClassVar[str] = "multi_account_kekkai_utilize_new_config"
    fade_conf: MultiAccountKekkaiUtilizeNew = None
    task_display_names: ClassVar[dict[str, str]] = {
        "MultiAccountKekkaiUtilizeNew": "多账号多任务蹭卡新",
    }
    retry_delay: ClassVar[timedelta] = timedelta(minutes=10)

    def run(self) -> None:
        logger.hr(self._current_task_display_name(), 1)
        self.fade_conf = getattr(self.config, self.multi_account_config_attr)
        self._account_scope = getattr(self, "current_account_info", None)
        now = datetime.now()

        if self._defer_for_server_update(now):
            self._refresh_outer_next_run()
            raise TaskEnd(self.task_name)

        pending = self._collect_pending_accounts(now)
        overall_failed = False
        for index, account in pending:
            self._yield_to_higher_priority_task()
            self.current_account_info = account
            logger.hr(f"处理账号 {account.character}-{account.svr}", 2)

            if not self._switch_account(account):
                overall_failed = True
                self._push_error_notification(
                    account,
                    "切换账号",
                    getattr(self, "_last_switch_error", None),
                )
                account.next_utilize_time = datetime.now() + self.retry_delay
                self._refresh_outer_next_run()
                continue

            if not self._run_utilize_with_retry(account):
                overall_failed = True
            self._refresh_outer_next_run()

        self._refresh_outer_next_run()
        if overall_failed:
            logger.warning("多账号多任务蹭卡新本轮存在失败账号，已分别安排重试")
        raise TaskEnd(self.task_name)

    def _collect_pending_accounts(
        self,
        now: datetime,
    ) -> list[tuple[int, MultiAccountKekkaiUtilizeNewAccount]]:
        """筛选到期账号，并在切号前处理公共/私有禁止蹭卡时段。"""
        pending: list[tuple[int, MultiAccountKekkaiUtilizeNewAccount]] = []
        for index, account in enumerate(self.fade_conf.account_list):
            if not self._sync_account_from_public(account) or not account.is_valid():
                continue
            if not self._is_account_in_scope(account):
                continue

            deferred = self._deferred_forbidden_time(account, account.next_utilize_time)
            if deferred is not None:
                account.next_utilize_time = deferred
                logger.info(
                    "%s-%s 的下一次蹭卡时间位于禁止时段，顺延到 %s",
                    account.character,
                    account.svr,
                    deferred,
                )
                continue
            if account.next_utilize_time > now:
                continue

            # 排队、模拟器启动较慢时，需再次按真实执行时间判断禁止时段。
            deferred = self._deferred_forbidden_time(account, now)
            if deferred is not None:
                account.next_utilize_time = deferred
                logger.info(
                    "%s-%s 当前处于禁止蹭卡时段，顺延到 %s",
                    account.character,
                    account.svr,
                    deferred,
                )
                continue
            pending.append((index, account))
        self._save_repeat_config()
        return pending

    def _run_utilize_with_retry(self, account: MultiAccountKekkaiUtilizeNewAccount) -> bool:
        """执行原结界蹭卡逻辑；失败重启并最多重试三次。"""
        last_exception: Exception | None = None
        for attempt in range(1, self.task_retry_limit + 1):
            backup = self._apply_account_utilize_config(account)
            captured: dict[str, datetime | None] = {"next_run": None}
            task_obj = None
            success = False
            try:
                task_obj = self.create_task_object(
                    "KekkaiUtilize",
                    config=self.config,
                    device=self.device,
                    current_account_info=account,
                )

                def capture(
                    task=None,
                    finish=False,
                    success=None,
                    server=True,
                    target=None,
                    **kwargs,
                ) -> None:
                    if task is not None and convert_to_underscore(str(task)) != "kekkai_utilize":
                        return
                    if isinstance(target, datetime):
                        captured["next_run"] = target
                        return
                    interval = (
                        self.fade_conf.scheduler.failure_interval
                        if success is False
                        else self.fade_conf.scheduler.success_interval
                    )
                    start = datetime.now() if finish else task_obj.start_time
                    captured["next_run"] = start + interval

                # 内层仅负责计算本账号下次蹭卡时间，不能写入公共结界蹭卡 scheduler。
                task_obj.set_next_run = capture
                task_obj.run()
                success = True
            except TaskEnd:
                success = getattr(task_obj, "_task_success", True)
            except RequestHumanTakeover:
                raise
            except Exception as exc:
                self.save_error_log()
                last_exception = exc
                logger.exception(
                    "结界蹭卡失败（%s-%s），第%s/%s次",
                    account.character,
                    account.svr,
                    attempt,
                    self.task_retry_limit,
                )
            finally:
                self._restore_account_utilize_config(backup)

            if success:
                next_time = captured["next_run"] or (datetime.now() + self.fade_conf.scheduler.success_interval)
                deferred = self._deferred_forbidden_time(account, next_time)
                account.next_utilize_time = deferred or next_time
                account.last_complete_time = datetime.now()
                logger.info(
                    "%s-%s 蹭卡完成，下次运行：%s",
                    account.character,
                    account.svr,
                    account.next_utilize_time,
                )
                return True
            if attempt < self.task_retry_limit:
                self._restart_game()

        account.next_utilize_time = datetime.now() + self.retry_delay
        self._push_error_notification(account, "结界蹭卡", last_exception)
        logger.warning(
            "%s-%s 蹭卡连续失败，安排在 %s 重试",
            account.character,
            account.svr,
            account.next_utilize_time,
        )
        return False

    def _apply_account_utilize_config(self, account: MultiAccountKekkaiUtilizeNewAccount):
        """临时套用账号独立蹭卡配置。"""
        backup = copy.deepcopy(self.config.kekkai_utilize.utilize_config)
        active = UtilizeConfig()
        for name, value in account.private_config.get("utilize_config", {}).items():
            if hasattr(active, name):
                setattr(active, name, value)
        try:
            active = UtilizeConfig.model_validate(active.model_dump())
        except ValidationError as exc:
            raise ValueError(f"账号私有蹭卡配置无效：{exc}") from exc
        self.config.kekkai_utilize.utilize_config = active
        return backup

    def _restore_account_utilize_config(self, backup: UtilizeConfig) -> None:
        self.config.kekkai_utilize.utilize_config = backup

    def _active_forbid_windows(
        self,
        account: MultiAccountKekkaiUtilizeNewAccount,
    ) -> list[tuple[Any, Any]]:
        """读取当前账号自行维护的禁止蹭卡时段。"""
        return [
            (period.start, period.end)
            for period in account.forbid_periods
            if period.start != period.end
        ]

    def _deferred_forbidden_time(
        self,
        account: MultiAccountKekkaiUtilizeNewAccount,
        reference_time: datetime,
    ) -> datetime | None:
        windows = self._active_forbid_windows(account)
        if not windows:
            return None
        intervals: list[tuple[datetime, datetime]] = []
        for offset in (-1, 0, 1):
            date = reference_time.date() + timedelta(days=offset)
            for start, end in windows:
                start_at = datetime.combine(date, start)
                end_at = datetime.combine(date, end)
                if end <= start:
                    end_at += timedelta(days=1)
                intervals.append((start_at, end_at))
        active = [(start, end) for start, end in intervals if start <= reference_time < end]
        if not active:
            return None
        forbidden_end = max(end for _, end in active)
        while True:
            extended = max(
                (end for start, end in intervals if start <= forbidden_end),
                default=forbidden_end,
            )
            if extended <= forbidden_end:
                break
            forbidden_end = extended
        return forbidden_end + timedelta(minutes=10)

    def _defer_for_server_update(self, now: datetime) -> bool:
        """维护期间顺延当前功能所有即将运行的账号，避免无效切号。"""
        if not is_server_update_window(now):
            return False
        target = build_server_update_delay_target(now)
        delayed = 0
        for account in self.fade_conf.account_list:
            if account.next_utilize_time < target:
                account.next_utilize_time = target
                delayed += 1
        if not delayed:
            return False
        logger.warning("服务器维护期间，已将 %s 个账号的蹭卡时间顺延到 %s", delayed, target)
        self._save_repeat_config()
        return True

    def _refresh_outer_next_run(self) -> None:
        next_runs = [
            account.next_utilize_time
            for account in self.fade_conf.account_list
            if account.is_valid() and account.next_utilize_time
        ]
        if next_runs:
            self.fade_conf.scheduler.next_run = min(next_runs).replace(microsecond=0)
        self._save_repeat_config()

    def _sync_account_from_public(self, account: MultiAccountKekkaiUtilizeNewAccount) -> bool:
        library = getattr(self.config, "multi_account_shared_accounts", None)
        source = library.find(account.public_account_identifier) if library is not None else None
        if source is None:
            logger.error("多账号多任务蹭卡新公共账号不存在：%s", account.public_account_identifier)
            return False
        account.sync_public_account(source)
        return True
