from __future__ import annotations

import copy
import random
from datetime import datetime, time, timedelta
from typing import Any, ClassVar

from module.config.model_overrides import model_with_field_overrides
from module.config.utils import (
    convert_to_underscore,
    parse_next_server_schedule,
)
from module.exception import RequestHumanTakeover, TaskEnd
from module.logger import logger
from pydantic import BaseModel, ValidationError
from tasks.MultiAccountTaskOrchestration.script_task import ScriptTask as MultiAccountRepeatNewBase
from tasks.Restart.server_update import build_server_update_delay_target, is_server_update_window


class AccountSchedulerTaskBase(MultiAccountRepeatNewBase):
    """账号独立调度型多账号任务的通用执行骨架。"""

    task_name: ClassVar[str]
    multi_account_config_attr: ClassVar[str]
    priority_config_attr: ClassVar[str]
    task_display_names: ClassVar[dict[str, str]]
    inner_task_name: ClassVar[str]
    inner_task_config_attr: ClassVar[str]
    settings_group: ClassVar[str]
    settings_model: ClassVar[type]
    next_run_field: ClassVar[str]
    overview_kind: ClassVar[str]
    action_label: ClassVar[str]
    persist_private_config_after_run: ClassVar[bool] = False
    retry_delay: ClassVar[timedelta] = timedelta(minutes=10)

    def run(self) -> None:
        logger.hr(self._current_task_display_name(), 1)
        self.fade_conf = getattr(self.config, self.multi_account_config_attr)
        self._account_scope = getattr(self, "current_account_info", None)
        now = datetime.now()

        if self._defer_for_server_update(now):
            self._refresh_outer_next_run()
            self._publish_overview(None)
            raise TaskEnd(self.task_name)

        pending = self._collect_pending_accounts(now)
        overall_failed = False
        for index, account in pending:
            self._yield_to_higher_priority_task()
            if not self._is_function_account_enabled(account, log_skip=True):
                continue
            if not self._is_shared_account_enabled(account, log_skip=True):
                continue
            self.current_account_info = account
            self._publish_overview({"account_index": index + 1})
            logger.hr(f"处理账号 {account.character}-{account.svr}", 2)

            if not self._switch_account(account):
                overall_failed = True
                self._push_error_notification(
                    account,
                    "切换账号",
                    getattr(self, "_last_switch_error", None),
                )
                self._schedule_account_next_run(account, success=False, start_time=datetime.now())
                self._refresh_outer_next_run()
                self._publish_overview(None)
                continue

            if not self._run_inner_with_retry(account):
                overall_failed = True
            self._refresh_outer_next_run()
            self._publish_overview(None)

        self._refresh_outer_next_run()
        self._publish_overview(None)
        if overall_failed:
            logger.warning(f"{self._current_task_display_name()}本轮存在失败账号，已分别安排重试")
        raise TaskEnd(self.task_name)

    def _collect_pending_accounts(self, now: datetime) -> list[tuple[int, Any]]:
        """筛选到期账号，并在切号前处理账号禁止时段。"""
        pending: list[tuple[int, Any]] = []
        for index, account in enumerate(self.fade_conf.account_list):
            if not self._prepare_runnable_account(account, log_skip=True):
                continue
            if not account.scheduler.enable:
                continue
            setattr(account, self.next_run_field, account.scheduler.next_run)
            deferred = self._deferred_forbidden_time(account, account.scheduler.next_run)
            if deferred is not None:
                self._set_account_next_run(account, deferred)
                logger.info(
                    "%s-%s 的下一次%s时间位于禁止时段，顺延到 %s",
                    account.character, account.svr, self.action_label, deferred,
                )
                continue
            if account.scheduler.next_run > now:
                continue
            deferred = self._deferred_forbidden_time(account, now)
            if deferred is not None:
                self._set_account_next_run(account, deferred)
                logger.info(
                    "%s-%s 当前处于禁止%s时段，顺延到 %s",
                    account.character, account.svr, self.action_label, deferred,
                )
                continue
            pending.append((index, account))
        pending.sort(
            key=lambda item: (
                item[1].scheduler.priority,
                item[1].scheduler.next_run,
                item[0],
            )
        )
        self._save_repeat_config()
        return pending

    def _run_inner_with_retry(self, account: Any) -> bool:
        """执行内层业务任务；失败重启并按统一上限重试。"""
        last_exception: Exception | None = None
        for attempt in range(1, self.task_retry_limit + 1):
            backup = self._apply_account_config(account)
            captured: dict[str, datetime | None] = {"next_run": None}
            task_obj = None
            success = False
            try:
                task_obj = self.create_task_object(
                    self.inner_task_name,
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
                    if task is not None and convert_to_underscore(str(task)) != convert_to_underscore(self.inner_task_name):
                        return
                    if isinstance(target, datetime):
                        captured["next_run"] = target
                        return
                    captured["next_run"] = self._calculate_account_next_run(
                        account,
                        success=success is not False,
                        start_time=datetime.now() if finish else task_obj.start_time,
                    )

                # 内层只计算当前账号下次运行时间，不能写入公共任务 Scheduler。
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
                    "%s失败（%s-%s），第%s/%s次",
                    self.action_label, account.character, account.svr,
                    attempt, self.task_retry_limit,
                )
            finally:
                self._before_restore_account_config(account)
                self._restore_account_config(backup)

            if success:
                next_time = captured["next_run"] or self._calculate_account_next_run(
                    account, success=True, start_time=datetime.now()
                )
                deferred = self._deferred_forbidden_time(account, next_time)
                self._set_account_next_run(account, deferred or next_time)
                account.last_complete_time = datetime.now()
                logger.info(
                    "%s-%s %s完成，下次运行：%s",
                    account.character, account.svr, self.action_label,
                    getattr(account, self.next_run_field),
                )
                return True
            if attempt < self.task_retry_limit:
                self._restart_game()

        self._schedule_account_next_run(account, success=False, start_time=datetime.now())
        self._push_error_notification(account, self.action_label, last_exception)
        logger.warning(
            "%s-%s %s连续失败，安排在 %s 重试",
            account.character, account.svr, self.action_label,
            getattr(account, self.next_run_field),
        )
        return False

    def _apply_account_config(self, account: Any):
        """临时套用账号私有配置，并由 Pydantic 统一转换存储格式。"""
        backup = copy.deepcopy(getattr(getattr(self.config, self.inner_task_config_attr), self.settings_group))
        if getattr(account.config_mode, "value", account.config_mode) == "public":
            return backup
        overrides = account.private_config.get(self.settings_group, {})
        if not isinstance(overrides, dict):
            raise ValueError(f"账号私有{self.action_label}配置无效：{self.settings_group} 必须是对象")

        # 私有配置来自 JSON/API，TimeDelta 等字段是字符串；不能先 setattr 到
        # 已类型化的模型后再 model_dump，否则序列化器会把字符串当 timedelta 使用。
        try:
            active = model_with_field_overrides(
                self.settings_model(),
                overrides,
                # 兼容历史配置中已经从当前业务模型移除的字段。
                ignore_unknown=True,
            )
        except (ValidationError, ValueError) as exc:
            raise ValueError(f"账号私有{self.action_label}配置无效：{exc}") from exc
        setattr(getattr(self.config, self.inner_task_config_attr), self.settings_group, active)
        return backup

    def _before_restore_account_config(self, account: Any) -> None:
        if not self.persist_private_config_after_run:
            return
        if getattr(account.config_mode, "value", account.config_mode) == "public":
            return
        active = getattr(getattr(self.config, self.inner_task_config_attr), self.settings_group)
        account.private_config = {self.settings_group: active.model_dump()}

    def _restore_account_config(self, backup: BaseModel) -> None:
        setattr(getattr(self.config, self.inner_task_config_attr), self.settings_group, backup)

    def _active_forbid_windows(
        self,
        account: Any,
    ) -> list[tuple[Any, Any]]:
        """读取当前账号自行维护的禁止运行时段。"""
        return [
            (period.start, period.end)
            for period in account.forbid_periods
            if period.start != period.end
        ]

    def _deferred_forbidden_time(
        self,
        account: Any,
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
            if not self._prepare_runnable_account(account):
                continue
            if account.scheduler.enable and account.scheduler.next_run < target:
                self._set_account_next_run(account, target)
                delayed += 1
        if not delayed:
            return False
        logger.warning("服务器维护期间，已将 %s 个账号的%s时间顺延到 %s", delayed, self.action_label, target)
        self._save_repeat_config()
        return True

    def _refresh_outer_next_run(self) -> None:
        next_runs = [
            account.scheduler.next_run
            for account in self.fade_conf.account_list
            if self._prepare_runnable_account(account) and account.scheduler.enable
        ]
        self.fade_conf.scheduler.next_run = (
            min(next_runs).replace(microsecond=0)
            if next_runs
            else datetime.max.replace(microsecond=0)
        )
        self._save_repeat_config()

    def _set_account_next_run(
        self, account: Any, next_run: datetime
    ) -> None:
        next_run = next_run.replace(microsecond=0)
        account.scheduler.next_run = next_run
        setattr(account, self.next_run_field, next_run)

    def _calculate_account_next_run(
        self,
        account: Any,
        *,
        success: bool,
        start_time: datetime,
        target: datetime | None = None,
    ) -> datetime:
        """将 OAS Config.task_delay 规则应用到账号虚拟 Scheduler。"""
        scheduler = account.scheduler
        next_run = (target or (start_time + (
            scheduler.success_interval if success else scheduler.failure_interval
        ))).replace(microsecond=0)
        float_time = scheduler.float_time
        random_float = random.randint(
            0, float_time.hour * 3600 + float_time.minute * 60 + float_time.second
        )
        if scheduler.server_update == time(hour=9):
            return next_run + timedelta(seconds=random_float)
        return parse_next_server_schedule(scheduler, random_float)

    def _schedule_account_next_run(
        self, account: Any, *, success: bool, start_time: datetime
    ) -> None:
        self._set_account_next_run(
            account,
            self._calculate_account_next_run(
                account, success=success, start_time=start_time
            ),
        )

    def _publish_overview(self, active: dict[str, int] | None) -> None:
        state_queue = getattr(self, "state_queue", None)
        if state_queue is not None:
            state_queue.put({
                "multi_account_overview": {"kind": self.overview_kind, "active": active}
            })
