from __future__ import annotations

import copy
from datetime import datetime, timedelta
from typing import Any, ClassVar

from module.config.utils import convert_to_underscore
from module.exception import RequestHumanTakeover, TaskEnd
from module.logger import logger
from pydantic import BaseModel, ValidationError
from tasks.MultiAccountRepeatNew.script_task import ScriptTask as MultiAccountRepeatNewBase
from tasks.Restart.server_update import (
    build_server_update_delay_target,
    is_server_update_window,
)
from tasks.MultiAccountRepeatTimed.config import (
    MultiAccountRepeatTimed,
    MultiAccountRepeatTimedAccount,
    MultiAccountRepeatTimedTask,
)


class ScriptTask(MultiAccountRepeatNewBase):
    """按“账号 × 任务”分别调度，并隔离任务运行记录的多账号执行器。"""

    task_name: ClassVar[str] = "MultiAccountRepeatTimed"
    multi_account_config_attr: ClassVar[str] = "multi_account_repeat_timed"
    priority_config_attr: ClassVar[str] = "multi_account_repeat_timed_config"
    fade_conf: MultiAccountRepeatTimed = None
    task_display_names: ClassVar[dict[str, str]] = {"MultiAccountRepeatTimed": "多账号多任务定时"}

    def run(self):
        logger.hr(self._current_task_display_name(), 1)
        self.fade_conf = getattr(self.config, self.multi_account_config_attr)
        now = datetime.now()
        overall_failed = False

        # 维护期间不进入任何账号任务，先把本 OAS 中所有到维护结束前的
        # 账号任务统一顺延，避免逐个账号尝试后重复报错。
        if self._defer_tasks_for_server_update(now):
            self._refresh_outer_next_run()
            raise TaskEnd(self.task_name)

        # 只对已到运行时间的账号任务按优先级排序；相同优先级时保持账号、
        # 任务列表原有顺序。这样 scheduler.priority 才会在定时版真正生效。
        due_plan = self._build_due_task_plan(now)
        if due_plan:
            logger.info(
                "本轮到期账号任务按优先级执行：%s",
                ", ".join(
                    f"{account.character}-{account.svr}/{entry.task_name}(P{priority})"
                    for _, _, priority, account, entry in due_plan
                ),
            )

        failed_account_ids: set[int] = set()
        current_account_id: int | None = None
        for _, _, _, account, entry in due_plan:
            self._yield_to_higher_priority_task()
            account_id = id(account)
            if account_id in failed_account_ids:
                continue

            # 只有需要处理另一个账号时才切号；因全局优先级排序可能在账号间往返。
            if current_account_id != account_id:
                logger.hr(f"处理账号 {account.character}-{account.svr}", 2)
                self.current_account_info = account
                if not self._switch_account(account):
                    overall_failed = True
                    self._push_error_notification(
                        account,
                        "切换账号",
                        getattr(self, "_last_switch_error", None),
                    )
                    # 当前账号本轮已到期的任务均无法执行，统一按失败间隔顺延。
                    for _, _, _, planned_account, planned_entry in due_plan:
                        if id(planned_account) != account_id:
                            continue
                        self._sync_entry_scheduler_next_run(
                            planned_entry,
                            self._fallback_next_run(
                                planned_entry.task_name,
                                planned_entry,
                                success=False,
                            ),
                        )
                    failed_account_ids.add(account_id)
                    current_account_id = None
                    self._refresh_outer_next_run(success=False)
                    continue
                current_account_id = account_id

            if not self._run_timed_task(account, entry):
                overall_failed = True
            # 每个账号任务结束后立即落盘，避免等待该账号其他任务结束，
            # 也避免外层调度器继续使用旧的 next_run 反复循环。
            self._refresh_outer_next_run(success=not overall_failed)

        self._refresh_outer_next_run(success=not overall_failed)
        raise TaskEnd(self.task_name)

    def _build_due_task_plan(
        self,
        now: datetime,
    ) -> list[tuple[int, int, int, MultiAccountRepeatTimedAccount, MultiAccountRepeatTimedTask]]:
        """获取已到期任务，并按“优先级、账号顺序、任务顺序”排序。"""
        due_plan: list[
            tuple[int, int, int, MultiAccountRepeatTimedAccount, MultiAccountRepeatTimedTask]
        ] = []
        for account_index, account in enumerate(self.fade_conf.account_list):
            if not self._sync_account_from_public(account) or not account.is_valid():
                continue
            for task_index, entry in enumerate(account.task_list):
                if not entry.task_name or entry.next_run > now:
                    continue
                priority = self._entry_priority(entry)
                due_plan.append((account_index, task_index, priority, account, entry))

        return sorted(due_plan, key=lambda item: (item[2], item[0], item[1]))

    def _entry_priority(self, entry: MultiAccountRepeatTimedTask) -> int:
        """读取账号任务私有 scheduler 的优先级，异常值回退为默认 5。"""
        scheduler = self._entry_scheduler(entry.task_name, entry)
        try:
            return int(getattr(scheduler, "priority", 5))
        except (TypeError, ValueError):
            return 5

    def _defer_tasks_for_server_update(self, now: datetime) -> bool:
        """维护期间顺延本 OAS 中所有即将运行的账号任务。"""
        if not is_server_update_window(now):
            return False

        delay_target = build_server_update_delay_target(now)
        delayed_tasks: list[str] = []
        for account in self.fade_conf.account_list:
            for entry in account.task_list:
                if not entry.task_name or entry.next_run >= delay_target:
                    continue
                self._sync_entry_scheduler_next_run(entry, delay_target)
                delayed_tasks.append(
                    f"{account.character}-{account.svr}/{entry.task_name}"
                )

        if not delayed_tasks:
            return False

        logger.warning(
            "当前处于服务器维护时间，已将本 OAS 的 %s 个账号任务顺延到 %s：%s",
            len(delayed_tasks),
            delay_target,
            ", ".join(delayed_tasks),
        )
        self._save_repeat_config()
        return True

    def _refresh_outer_next_run(self, success: bool | None = None) -> None:
        """立即刷新并保存外层任务的 next_run，取所有账号任务的最早时间。"""
        next_runs = [
            entry.next_run
            for account in self.fade_conf.account_list
            for entry in account.task_list
            if entry.task_name and entry.next_run
        ]
        target = min(next_runs) if next_runs else None
        if target is not None:
            # 先保存账号任务，再保存外层时间；不能调用 task_delay 后再保存，
            # 因为 task_delay 会 reload 配置，可能覆盖刚刚更新的账号任务。
            self.fade_conf.scheduler.next_run = target.replace(microsecond=0)
            self._save_repeat_config()
            logger.attr(f"{self.task_name}.scheduler.next_run", self.fade_conf.scheduler.next_run)
            return

        # 没有任何账号任务时，仍按外层任务的成功/失败间隔安排下一次运行。
        self.set_next_run(self.task_name, success=success, server=False)
        self._save_repeat_config()

    @staticmethod
    def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
        """递归合并运行记录；运行记录只覆盖本账号本任务已产生的状态。"""
        result = copy.deepcopy(base)
        for key, value in override.items():
            if isinstance(value, dict) and isinstance(result.get(key), dict):
                result[key] = ScriptTask._deep_merge(result[key], value)
            else:
                result[key] = copy.deepcopy(value)
        return result

    @classmethod
    def _runtime_diff(cls, before: Any, after: Any, path: tuple[str, ...] = ()) -> Any:
        """提取任务运行中改变的配置字段，不保存公共调度器状态。"""
        if path == ("scheduler",):
            return None
        if isinstance(before, dict) and isinstance(after, dict):
            diff: dict[str, Any] = {}
            for key, value in after.items():
                changed = cls._runtime_diff(before.get(key), value, path + (str(key),))
                if changed is not None:
                    diff[key] = changed
            return diff or None
        if before != after:
            return copy.deepcopy(after)
        return None

    @staticmethod
    def _has_private_task_overrides(private_config: dict[str, Any]) -> bool:
        """判断私有配置是否包含用户设置，自动记录的 next_run 不算私有覆盖。"""
        for group_name, arguments in private_config.items():
            if not isinstance(arguments, dict):
                continue
            if convert_to_underscore(group_name) == "scheduler" and set(arguments) <= {"next_run"}:
                continue
            return True
        return False

    def _apply_timed_task_config(
        self,
        task_name: str,
        task_entry: MultiAccountRepeatTimedTask,
    ) -> tuple[str, BaseModel, dict[str, Any]]:
        """临时套用私有配置和该账号的运行记录，并保存完整公共配置备份。"""
        task_key = convert_to_underscore(task_name)
        public_config = getattr(self.config.model, task_key, None)
        if not isinstance(public_config, BaseModel):
            raise ValueError(f"找不到任务配置：{task_name}")

        public_backup = copy.deepcopy(public_config)
        # 私有配置只覆盖默认配置，不能把已经修改过的公共配置带入账号任务。
        active = (
            public_config.__class__()
            if self._has_private_task_overrides(task_entry.private_config)
            else copy.deepcopy(public_config)
        )
        for group_name, arguments in task_entry.private_config.items():
            if not isinstance(arguments, dict):
                continue
            for argument_name, value in arguments.items():
                self._set_task_argument(active, group_name, argument_name, value)
        try:
            active = active.__class__.model_validate(active.model_dump())
        except ValidationError as exc:
            raise ValueError(f"账号私有配置无效：{task_name}: {exc}") from exc

        baseline = active.model_dump()
        if task_entry.runtime_record:
            active = active.__class__.model_validate(
                self._deep_merge(baseline, task_entry.runtime_record)
            )
        BaseModel.__setattr__(self.config.model, task_key, active)
        logger.info(
            "为账号 %s-%s 套用任务 %s 的私有配置和运行记录",
            self.current_account_info.character,
            self.current_account_info.svr,
            task_name,
        )
        return task_key, public_backup, active.model_dump()

    def _restore_timed_task_config(self, backup_info: tuple[str, BaseModel, dict[str, Any]] | None) -> None:
        """恢复完整公共配置，防止任一账号的运行记录写入公共配置或其他账号。"""
        if backup_info is None:
            return
        task_key, public_backup, _ = backup_info
        BaseModel.__setattr__(self.config.model, task_key, public_backup)
        self.config.save_selected_fields({task_key: public_backup})

    def _entry_scheduler(self, task_name: str, entry: MultiAccountRepeatTimedTask):
        """读取当前账号任务的私有调度器，未私有化的字段继承公共配置。"""
        task_config = getattr(self.config.model, convert_to_underscore(task_name), None)
        scheduler_config = getattr(task_config, "scheduler", None)
        if scheduler_config is not None and self._has_private_task_overrides(entry.private_config):
            scheduler = scheduler_config.__class__()
        else:
            scheduler = copy.deepcopy(scheduler_config)
        private = entry.private_config.get("scheduler", {})
        if scheduler is not None and isinstance(private, dict):
            for name, value in private.items():
                if hasattr(scheduler, name):
                    setattr(scheduler, name, value)
        return scheduler

    def _sync_entry_scheduler_next_run(
        self, entry: MultiAccountRepeatTimedTask, next_run: datetime
    ) -> None:
        """任务实际下次时间同时保存到私有调度器，供设置页读取和比较。"""
        entry.next_run = next_run
        entry.private_config.setdefault("scheduler", {})["next_run"] = next_run.strftime(
            "%Y-%m-%d %H:%M:%S"
        )

    def _fallback_next_run(
        self, task_name: str, entry: MultiAccountRepeatTimedTask, success: bool
    ) -> datetime:
        """任务未主动设置下次运行时间时，按该账号任务的调度间隔推算。"""
        scheduler = self._entry_scheduler(task_name, entry)
        interval = getattr(
            scheduler,
            "success_interval" if success else "failure_interval",
            None,
        )
        if interval is None:
            interval = timedelta(minutes=10)
        return datetime.now() + interval

    def _run_timed_task(
        self,
        account: MultiAccountRepeatTimedAccount,
        entry: MultiAccountRepeatTimedTask,
    ) -> bool:
        """运行一项到期任务，并把配置型运行记录保存到该账号的任务项中。"""
        task_name = entry.task_name
        last_exception: Exception | None = None
        for attempt in range(1, self.task_retry_limit + 1):
            backup = self._apply_timed_task_config(task_name, entry)
            task_key, _, baseline = backup
            previous = getattr(self.config, "_save_selected_fields", None)
            self.config._save_selected_fields = {task_key}
            task_obj = None
            task_success = False
            captured = {"next_run": None}
            try:
                task_obj = self.create_task_object(
                    task_name,
                    config=self.config,
                    device=self.device,
                    current_account_info=account,
                )

                def capture(
                    inner_task=None,
                    finish=False,
                    success=None,
                    server=True,
                    target=None,
                    task=None,
                    **kwargs,
                ):
                    # 不同任务有时使用位置参数，有时使用 task=；统一转换后再判断。
                    requested_task = task if task is not None else inner_task
                    if requested_task is not None and convert_to_underscore(str(requested_task)) != convert_to_underscore(task_name):
                        return
                    if target is not None:
                        captured["next_run"] = target
                        return
                    interval_success = success is not False
                    scheduler = getattr(
                        getattr(self.config, task_key, None),
                        "scheduler",
                        None,
                    )
                    interval = getattr(
                        scheduler,
                        "success_interval" if interval_success else "failure_interval",
                        None,
                    )
                    if interval is None:
                        interval = timedelta(minutes=10)
                    start = datetime.now() if finish else task_obj.start_time
                    captured["next_run"] = start + interval

                # 内层任务的 set_next_run 只更新当前账号当前任务，不改公共调度器。
                task_obj.set_next_run = capture
                task_obj.run()
                task_success = True
            except TaskEnd:
                task_success = getattr(task_obj, "_task_success", True)
            except RequestHumanTakeover:
                raise
            except Exception as exc:
                self.save_error_log()
                last_exception = exc
                logger.exception(
                    "定时任务 %s（%s-%s）失败，第%s/%s次",
                    task_name,
                    account.character,
                    account.svr,
                    attempt,
                    self.task_retry_limit,
                )
                task_success = False
            finally:
                if task_success:
                    active = getattr(self.config.model, task_key, None)
                    if isinstance(active, BaseModel):
                        entry.runtime_record = self._runtime_diff(baseline, active.model_dump()) or {}
                    self._sync_entry_scheduler_next_run(
                        entry,
                        captured["next_run"]
                        or self._fallback_next_run(task_name, entry, success=True),
                    )
                self._restore_timed_task_config(backup)
                self.config._save_selected_fields = previous

            if task_success:
                logger.info(
                    "%s-%s 的定时任务 %s 执行结束，下次运行：%s",
                    account.character,
                    account.svr,
                    task_name,
                    entry.next_run,
                )
                return True
            if attempt < self.task_retry_limit:
                self._restart_game()

        self._push_error_notification(account, self._task_display_name(task_name), last_exception)
        self._sync_entry_scheduler_next_run(
            entry, self._fallback_next_run(task_name, entry, success=False)
        )
        return False
