import copy
import importlib.util
import re
import random
from hashlib import sha256
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any, ClassVar

from module.exception import RequestHumanTakeover, TaskEnd
from module.logger import logger
from pydantic import BaseModel, ValidationError
from module.config.model_overrides import model_with_field_overrides, model_with_group_overrides
from module.config.utils import convert_to_underscore, parse_next_server_weekday, parse_tomorrow_server
from tasks.MultiAccountTaskOrchestration.task_name_resolver import TaskNameResolver
from tasks.Restart.server_update import build_server_update_delay_target, is_server_update_window
from tasks.Component.MultiAccount.multi_account_priority import MultiAccountPriorityMixin
from tasks.Component.SwitchAccount.assets import SwitchAccountAssets
from tasks.Component.SwitchAccount.tree_switch_account import TreeSwitchAccount
from tasks.GameUi.game_ui import GameUi
from tasks.MultiAccountTaskOrchestration.assets import MultiAccountRepeatNewAssets
from tasks.MultiAccountTaskOrchestration.config import (
    MultiAccountTaskOrchestration,
    MultiAccountRepeatNewAccount,
    MultiAccountRepeatNewFixedTimeBatch,
    MultiAccountRepeatNewFixedTimeBatchProgress,
    MultiAccountRepeatNewFixedTimeBatchTask,
    MultiAccountRepeatNewTask,
)


class ScriptTask(MultiAccountPriorityMixin, GameUi, MultiAccountRepeatNewAssets, SwitchAccountAssets):
    """多账号任务编排的统一执行器。"""

    task_name: ClassVar[str] = "MultiAccountTaskOrchestration"
    multi_account_config_attr: ClassVar[str] = "multi_account_task_orchestration"
    priority_config_attr: ClassVar[str] = "multi_account_repeat_new_config"
    fade_conf: MultiAccountTaskOrchestration = None
    overview_kind: ClassVar[str] = "orchestration"
    task_retry_limit: ClassVar[int] = 3
    task_display_names: ClassVar[dict[str, str]] = {
        "MultiAccountTaskOrchestration": "多账号任务编排",
    }

    def _publish_multi_account_overview(
        self,
        kind: str,
        active: dict[str, Any] | None,
    ) -> None:
        """Publishes virtual multi-account execution state over OAS's native queue."""
        state_queue = getattr(self, "state_queue", None)
        if state_queue is not None:
            state_queue.put({"multi_account_overview": {"kind": kind, "active": active}})

    def run(self):
        logger.hr(self._current_task_display_name(), 1)
        self.fade_conf = getattr(self.config, self.multi_account_config_attr)
        self._account_scope = getattr(self, "current_account_info", None)
        # 普通列表和混合版进入账号前统一拦截维护时间，避免切号后才发现停服。
        if self._delay_for_server_update_before_accounts():
            raise TaskEnd(self.task_name)
        if self._has_orchestration_items():
            return self._run_orchestration_items()
        overall_failed = False

        for account_info in self.fade_conf.account_list:
            self._yield_to_higher_priority_task()
            if not self._sync_account_from_public(account_info):
                logger.error("多账号任务编排公共账号不存在：%s", account_info.public_account_identifier)
                overall_failed = True
                continue
            if not account_info.is_valid():
                continue
            if not self._is_account_in_scope(account_info):
                continue
            logger.hr(f"处理账号 {account_info.character}-{account_info.svr}", 2)
            logger.info("开始处理账号 %s-%s", account_info.character, account_info.svr)

            failed_task_names = account_info.failed_task_names
            unfinished_task_names = account_info.unfinished_task_names
            if not failed_task_names and not unfinished_task_names:
                last_complete_time = account_info.last_complete_time
                now = datetime.now()
                if last_complete_time.date() == now.date():
                    logger.warning(
                        "%s-%s 今天已经执行过，跳过本次任务",
                        account_info.character,
                        account_info.svr,
                    )
                    continue

            if not self._switch_account(account_info):
                overall_failed = True
                logger.warning(
                    "切换到账号 %s-%s 失败",
                    account_info.character,
                    account_info.svr,
                )
                self._push_error_notification(account_info, "切换账号", getattr(self, "_last_switch_error", None))
                continue

            task_names = self._get_task_names_to_run(
                account_info,
                failed_task_names,
                unfinished_task_names,
            )
            # 清除已从任务列表移除的旧记录后，重新读取状态，避免旧失败项阻止账号完成。
            failed_task_names = account_info.failed_task_names
            unfinished_task_names = account_info.unfinished_task_names
            if not task_names:
                logger.warning(
                    "%s-%s 没有配置需要执行的任务",
                    account_info.character,
                    account_info.svr,
                )
                self._save_task_progress(account_info, [], [], [])
                continue

            # 成功、失败和未完成状态分别保存。中断时只会保留当前项及其后的未完成项。
            remaining_failed_task_names = list(dict.fromkeys(failed_task_names))
            completed_task_names = list(dict.fromkeys(account_info.completed_task_names))
            if not failed_task_names and not unfinished_task_names:
                # 新一轮完整执行不沿用上轮的成功清单。
                completed_task_names = []

            for task_index, task_name in enumerate(task_names):
                # 在真正开始前持久化检查点；外部停止、强制退出或人工接管时可从当前项恢复。
                self._save_task_progress(
                    account_info,
                    completed_task_names,
                    remaining_failed_task_names,
                    task_names[task_index:],
                )
                task_entry = self._get_task_entry(account_info, task_name)
                if self._run_task_with_retry(account_info, task_name, task_entry):
                    remaining_failed_task_names = [
                        failed_task
                        for failed_task in remaining_failed_task_names
                        if failed_task != task_name
                    ]
                    if task_name not in completed_task_names:
                        completed_task_names.append(task_name)
                    # 当前项成功后，与下一项检查点一起提交，避免交接时重复执行。
                    self._save_task_progress(
                        account_info,
                        completed_task_names,
                        remaining_failed_task_names,
                        task_names[task_index + 1:],
                    )
                    continue

                if task_name not in remaining_failed_task_names:
                    remaining_failed_task_names.append(task_name)
                completed_task_names = [
                    completed_task
                    for completed_task in completed_task_names
                    if completed_task != task_name
                ]
                # 失败已经有明确结果，不再属于“中断未完成”；仅后续未开始任务保留检查点。
                self._save_task_progress(
                    account_info,
                    completed_task_names,
                    remaining_failed_task_names,
                    task_names[task_index + 1:],
                )
                overall_failed = True

            if remaining_failed_task_names:
                self._save_task_progress(
                    account_info,
                    completed_task_names,
                    remaining_failed_task_names,
                    [],
                )
                continue

            # 整个账号本轮完成时，成功清单按当前任务列表归一，失败/中断清单清空。
            self.fade_conf.update_account_login_history(account_info)
            self._save_task_progress(
                account_info,
                account_info.task_names,
                [],
                [],
            )

        incomplete_accounts = self._get_incomplete_accounts_today()
        if (
            self.fade_conf.multi_account_repeat_new_config.rerun_incomplete_accounts
            and not getattr(self, "_rerun_incomplete_accounts_done", False)
            and incomplete_accounts
        ):
            logger.warning(
                "本轮结束后仍有账号今天未完成任务，重新执行一次多账号任务编排: %s",
                ", ".join(incomplete_accounts),
            )
            # 第二次直接复用完整流程，由“今日已执行则跳过”自动跳过已完成账号。
            self._rerun_incomplete_accounts_done = True
            return self.run()

        self.set_next_run(self.task_name, success=not overall_failed)
        raise TaskEnd(self.task_name)

    def _delay_for_server_update_before_accounts(self) -> bool:
        """维护期间不进入账号列表，外层任务延后到维护结束后再统一执行。"""
        now = datetime.now()
        if not is_server_update_window(now):
            return False

        delay_target = build_server_update_delay_target(now)
        logger.warning(
            "%s 当前处于服务器维护时间，跳过本轮账号任务，延后到 %s",
            self._current_task_display_name(),
            delay_target,
        )
        # 此时尚未修改账号进度，直接写外层 scheduler 即可，不记录任务成功。
        self.set_next_run(self.task_name, success=None, server=False, target=delay_target)
        return True

    def _single_task_scheduler(self, entry: MultiAccountRepeatNewTask):
        """独立单任务复用自身私有 Scheduler，和多账号定时使用同一覆盖方式。"""
        task_config = getattr(self.config.model, convert_to_underscore(entry.task_name), None)
        public_scheduler = getattr(task_config, "scheduler", None)
        if public_scheduler is None:
            return None
        private_scheduler = entry.private_config.get("scheduler", {})
        return model_with_field_overrides(
            public_scheduler,
            private_scheduler if isinstance(private_scheduler, dict) else {},
        )

    def _enabled_single_tasks(self, account_info: MultiAccountRepeatNewAccount):
        return [
            entry for entry in account_info.task_list
            if entry.task_name
            and (scheduler := self._single_task_scheduler(entry)) is not None
            and scheduler.enable
        ]

    def _has_orchestration_items(self) -> bool:
        """只要配置过任务组或独立单任务，就使用编排模式；未启用项绝不回退为旧普通任务执行。"""
        return any(
            batch.batch_id or batch.task_list
            for account in self.fade_conf.account_list
            for batch in account.fixed_time_batch_list
        ) or any(
            entry.task_name and isinstance(entry.private_config.get("scheduler"), dict)
            for account in self.fade_conf.account_list
            for entry in account.task_list
        )

    @staticmethod
    def _enabled_fixed_time_batches(account_info: MultiAccountRepeatNewAccount) -> list[MultiAccountRepeatNewFixedTimeBatch]:
        return [batch for batch in account_info.fixed_time_batch_list if batch.scheduler.enable and batch.task_names]

    @staticmethod
    def _batch_progress_for_today(batch: MultiAccountRepeatNewFixedTimeBatch) -> MultiAccountRepeatNewFixedTimeBatchProgress:
        if batch.task_progress_time.date() != datetime.now().date():
            return MultiAccountRepeatNewFixedTimeBatchProgress()
        return MultiAccountRepeatNewFixedTimeBatchProgress(
            progress_time=batch.task_progress_time,
            completed_task_list=batch.completed_task_list,
            failed_task_list=batch.failed_task_list,
            unfinished_task_list=batch.unfinished_task_list,
        )

    def _fixed_batch_task_names_to_run(self, batch: MultiAccountRepeatNewFixedTimeBatch) -> list[str]:
        """恢复该顺序任务组自己的失败/中断项，已完成项不重复运行。"""
        configured = batch.task_names
        progress = self._batch_progress_for_today(batch)
        recovery = set(progress.failed_task_names) | set(progress.unfinished_task_names)
        if recovery:
            return [name for name in configured if name in recovery]
        if batch.last_complete_time.date() == datetime.now().date():
            return []
        return [name for name in configured if name not in set(progress.completed_task_names)]

    def _save_fixed_batch_progress(
        self,
        batch: MultiAccountRepeatNewFixedTimeBatch,
        completed_task_names: list[str],
        failed_task_names: list[str],
        unfinished_task_names: list[str],
    ) -> None:
        """状态完全归属到顺序任务组，不再与同账号其他任务组共享。"""
        batch.task_progress_time = datetime.now()
        batch.completed_task_list = "\n".join(self._task_display_name(name) for name in dict.fromkeys(completed_task_names))
        batch.failed_task_list = "\n".join(self._task_display_name(name) for name in dict.fromkeys(failed_task_names))
        batch.unfinished_task_list = "\n".join(self._task_display_name(name) for name in dict.fromkeys(unfinished_task_names))
        self._save_repeat_config()

    @staticmethod
    def _scheduler_next_run(scheduler, *, success: bool) -> datetime:
        """复用 OAS Scheduler 成功/失败后的完整时间计算语义。"""
        now = datetime.now().replace(microsecond=0)
        interval = scheduler.success_interval if success else scheduler.failure_interval
        next_run = now + interval
        float_time = scheduler.float_time
        random_float = random.randint(0, float_time.hour * 3600 + float_time.minute * 60 + float_time.second)
        if scheduler.server_update == time(hour=9):
            return next_run + timedelta(seconds=random_float)
        mode = getattr(scheduler.schedule_mode, "value", scheduler.schedule_mode)
        if mode == "weekday":
            return parse_next_server_weekday(scheduler.server_update, scheduler.weekdays, random_float)
        return parse_tomorrow_server(scheduler.server_update, scheduler.delay_date, random_float)

    def _build_due_fixed_batch_plan(self, now: datetime):
        """收集账号下到点的顺序任务组，按原生 Scheduler 规则排序。"""
        due_plan = []
        for account_index, account_info in enumerate(self.fade_conf.account_list):
            if not self._sync_account_from_public(account_info) or not account_info.is_valid() or not self._is_account_in_scope(account_info):
                continue
            for batch_index, batch in enumerate(self._enabled_fixed_time_batches(account_info)):
                if batch.scheduler.next_run > now:
                    continue
                task_names = self._fixed_batch_task_names_to_run(batch)
                if task_names:
                    due_plan.append((account_index, batch_index, account_info, batch, task_names))
        return sorted(due_plan, key=lambda item: (item[3].scheduler.priority, item[3].scheduler.next_run, item[0], item[1]))

    def _run_orchestration_items(self):
        """按同一 Scheduler 队列运行任务组与独立单任务。"""
        now = datetime.now()
        if self._delay_for_server_update_before_accounts():
            raise TaskEnd(self.task_name)
        due_plan = []
        for account_index, account_info in enumerate(self.fade_conf.account_list):
            if not self._sync_account_from_public(account_info) or not account_info.is_valid() or not self._is_account_in_scope(account_info):
                continue
            for item_index, batch in enumerate(self._enabled_fixed_time_batches(account_info)):
                if batch.scheduler.next_run <= now and (task_names := self._fixed_batch_task_names_to_run(batch)):
                    due_plan.append((batch.scheduler.priority, batch.scheduler.next_run, account_index, item_index, "group", account_info, batch, task_names))
            for item_index, entry in enumerate(self._enabled_single_tasks(account_info)):
                scheduler = self._single_task_scheduler(entry)
                if scheduler is not None and scheduler.next_run <= now:
                    due_plan.append((scheduler.priority, scheduler.next_run, account_index, item_index, "single", account_info, entry, [entry.task_name]))
        due_plan.sort(key=lambda item: item[:4])
        failed_account_ids: set[int] = set()
        current_account_id: int | None = None
        for _, _, account_index, _, item_type, account_info, item, task_names in due_plan:
            self._yield_to_higher_priority_task()
            account_id = id(account_info)
            if account_id in failed_account_ids:
                continue
            if current_account_id != account_id:
                self.current_account_info = account_info
                if not self._switch_account(account_info):
                    failed_account_ids.add(account_id)
                    current_account_id = None
                    continue
                current_account_id = account_id
            payload = {"account_index": account_index + 1}
            if item_type == "group":
                payload["batch_id"] = item.batch_id
            else:
                payload["task_name"] = item.task_name
            self._publish_multi_account_overview(self.overview_kind, payload)
            if item_type == "group":
                self._run_fixed_time_batch(account_info, item, task_names)
            else:
                self._run_single_orchestration_task(account_info, item)
            self._publish_multi_account_overview(self.overview_kind, None)
        next_run = self._next_orchestration_item_run()
        self.set_next_run(self.task_name, success=None, server=False, target=next_run) if next_run else self.set_next_run(self.task_name, success=True, server=False)
        raise TaskEnd(self.task_name)

    def _run_single_orchestration_task(self, account_info: MultiAccountRepeatNewAccount, entry: MultiAccountRepeatNewTask) -> bool:
        scheduler = self._single_task_scheduler(entry)
        if scheduler is None:
            return False
        entry.task_progress_time = datetime.now()
        entry.status = "running"
        self._save_repeat_config()
        success = self._run_task_with_retry(account_info, entry.task_name, entry)
        entry.task_progress_time = datetime.now()
        entry.status = "completed" if success else "failed"
        if success:
            entry.last_complete_time = datetime.now()
        scheduler.next_run = self._scheduler_next_run(scheduler, success=success)
        entry.private_config.setdefault("scheduler", {}).update(scheduler.model_dump(mode="json"))
        self._save_repeat_config()
        return success

    def _run_fixed_time_batch(self, account_info: MultiAccountRepeatNewAccount, batch: MultiAccountRepeatNewFixedTimeBatch, task_names: list[str]) -> bool:
        progress = self._batch_progress_for_today(batch)
        completed = list(dict.fromkeys(progress.completed_task_names))
        failed = list(dict.fromkeys(progress.failed_task_names))
        overall_failed = False
        for index, task_name in enumerate(task_names):
            self._save_fixed_batch_progress(batch, completed, failed, task_names[index:])
            if self._run_task_with_retry(account_info, task_name, batch.task_entry(task_name)):
                failed = [name for name in failed if name != task_name]
                if task_name not in completed:
                    completed.append(task_name)
                self._save_fixed_batch_progress(batch, completed, failed, task_names[index + 1:])
                continue
            if task_name not in failed:
                failed.append(task_name)
            completed = [name for name in completed if name != task_name]
            self._save_fixed_batch_progress(batch, completed, failed, task_names[index + 1:])
            overall_failed = True
        if overall_failed:
            batch.scheduler.next_run = self._scheduler_next_run(batch.scheduler, success=False)
            self._save_repeat_config()
            return False
        batch.last_complete_time = datetime.now()
        batch.scheduler.next_run = self._scheduler_next_run(batch.scheduler, success=True)
        self._save_fixed_batch_progress(batch, batch.task_names, [], [])
        return True

    def _next_orchestration_item_run(self) -> datetime | None:
        targets = [
            batch.scheduler.next_run
            for account in self.fade_conf.account_list
            if self._is_account_in_scope(account)
            for batch in self._enabled_fixed_time_batches(account)
        ]
        targets.extend(
            scheduler.next_run
            for account in self.fade_conf.account_list
            if self._is_account_in_scope(account)
            for entry in self._enabled_single_tasks(account)
            if (scheduler := self._single_task_scheduler(entry)) is not None
        )
        return min(targets) if targets else None

    @staticmethod
    def _is_account_completed_today(account_info: MultiAccountRepeatNewAccount) -> bool:
        """根据上次完整完成时间判断账号今天是否已完成。"""
        return account_info.last_complete_time.date() == datetime.now().date()

    def _get_incomplete_accounts_today(self) -> list[str]:
        """返回当前执行范围内今天尚未完整完成任务的有效账号。"""
        accounts = []
        for account_info in self.fade_conf.account_list:
            if not self._sync_account_from_public(account_info):
                continue
            if not account_info.is_valid() or not self._is_account_in_scope(account_info):
                continue
            if not self._is_account_completed_today(account_info):
                accounts.append(f"{account_info.character}-{account_info.svr}")
        return accounts

    def _sync_account_from_public(self, account_info: MultiAccountRepeatNewAccount) -> bool:
        """从可复用的公共账号库同步登录信息。"""
        library = getattr(self.config, "multi_account_shared_accounts", None)
        source = library.find(account_info.public_account_identifier) if library is not None else None
        if source is None:
            return False
        account_info.sync_public_account(source)
        return True

    def _save_repeat_config(self) -> None:
        """只保存当前循环任务状态，避免覆盖页面刚修改的其他配置。"""
        self.config.save_selected_fields({
            self.multi_account_config_attr: self.fade_conf,
        })

    def _save_task_progress(
        self,
        account_info: MultiAccountRepeatNewAccount,
        completed_task_names: list[str],
        failed_task_names: list[str],
        unfinished_task_names: list[str],
    ) -> None:
        """一次性提交账号任务状态，避免停止时成功、失败、检查点三类记录彼此不一致。"""
        account_info.completed_task_list = "\n".join(
            self._task_display_name(task_name)
            for task_name in dict.fromkeys(completed_task_names)
        )
        account_info.failed_task_list = "\n".join(
            self._task_display_name(task_name)
            for task_name in dict.fromkeys(failed_task_names)
        )
        account_info.unfinished_task_list = "\n".join(
            self._task_display_name(task_name)
            for task_name in dict.fromkeys(unfinished_task_names)
        )
        # 状态列表只代表当天本轮执行进度，供任务页面准确展示“今日已完成/未完成”。
        account_info.task_progress_time = datetime.now()
        self._save_repeat_config()

    def _get_task_names_to_run(
        self,
        account_info: MultiAccountRepeatNewAccount,
        failed_task_names: list[str],
        unfinished_task_names: list[str],
    ) -> list[str]:
        """优先恢复上次失败或中断的任务，避免重复执行已成功任务。"""
        configured_task_names = account_info.task_names
        recovery_task_names = [*failed_task_names, *unfinished_task_names]
        if not recovery_task_names:
            return configured_task_names

        task_names = [
            task_name
            for task_name in configured_task_names
            if task_name in recovery_task_names
        ]
        removed_task_names = [
            task_name
            for task_name in recovery_task_names
            if task_name not in configured_task_names
        ]
        if removed_task_names:
            logger.warning(
                "%s-%s 的失败或未完成任务已不在当前任务列表中，清除记录: %s",
                account_info.character,
                account_info.svr,
                ", ".join(removed_task_names),
            )
            self._save_task_progress(
                account_info,
                [task_name for task_name in account_info.completed_task_names if task_name in configured_task_names],
                [task_name for task_name in failed_task_names if task_name in configured_task_names],
                [task_name for task_name in unfinished_task_names if task_name in configured_task_names],
            )

        if task_names:
            logger.info(
                "%s-%s 上次失败或未完成任务，本次继续执行: %s",
                account_info.character,
                account_info.svr,
                ", ".join(self._task_display_name(name) for name in task_names),
            )
            return task_names
        return configured_task_names

    @staticmethod
    def _task_display_name(task_name: str) -> str:
        """将内部任务名转换为配置中使用的中文任务名。"""
        aliases = TaskNameResolver._build_aliases(task_name)
        return next((name for name in aliases if any('\u4e00' <= char <= '\u9fff' for char in name)), task_name)

    def _current_task_display_name(self) -> str:
        return self.task_display_names.get(self.task_name, self.task_name)

    def _push_error_notification(self, account_info, failed_task: str, exc: Exception | None = None) -> None:
        account_name = f"{account_info.character}-{account_info.svr}"
        error_type = type(exc).__name__ if exc is not None else "切换失败"
        current_task = self._current_task_display_name()
        self.config.notifier.push(
            title=f"{current_task}报错：{account_name}",
            content=(
                f"当前功能：{current_task}\n"
                f"报错账号：{account_name}\n"
                f"报错功能：{failed_task}\n"
                f"报错类型：{error_type}"
            ),
        )

    def _run_task_with_retry(
        self,
        account_info: MultiAccountRepeatNewAccount,
        task_name: str,
        task_entry: MultiAccountRepeatNewTask | MultiAccountRepeatNewFixedTimeBatchTask | None = None,
    ) -> bool:
        """执行单个任务；失败后重启游戏并最多重试三次。"""
        self.current_account_info = account_info
        last_exception: Exception | None = None
        for attempt in range(1, self.task_retry_limit + 1):
            if attempt > 1:
                logger.warning(
                    "%s-%s 的任务 %s 失败，重启游戏后重试（第 %s/%s 次）",
                    account_info.character,
                    account_info.svr,
                    task_name,
                    attempt,
                    self.task_retry_limit,
                )
                # 重启不会改变当前登录账号，直接重试当前任务即可。
                self._restart_game()

            logger.info(
                "为账号 %s-%s 执行多账号多任务项 %s（第 %s/%s 次）",
                account_info.character,
                account_info.svr,
                task_name,
                attempt,
                self.task_retry_limit,
            )
            previous_save_fields = getattr(
                self.config,
                "_save_selected_fields",
                None,
            )
            # 内层任务保存运行状态时，仅合并写回自身配置，不能覆盖其他多账号功能。
            self.config._save_selected_fields = {convert_to_underscore(task_name)}
            task_config_backup = self._apply_private_task_config(task_name, task_entry)
            task_success = False
            try:
                task_obj = self.create_task_object(
                    task_name,
                    config=self.config,
                    device=self.device,
                    current_account_info=account_info,
                )
                task_obj.run()
                task_success = True
                return True
            except TaskEnd:
                task_success = getattr(task_obj, "_task_success", True)
                if task_success:
                    logger.info(
                        "%s-%s 的任务 %s 执行结束",
                        account_info.character,
                        account_info.svr,
                        task_name,
                    )
                    if getattr(task_obj, "_restart_before_next_task", False):
                        logger.warning(
                            "%s-%s 的任务 %s 已完成，但收尾环境未清理，重启游戏后执行下一项",
                            account_info.character,
                            account_info.svr,
                            task_name,
                        )
                        self._restart_game()
                else:
                    last_exception = RuntimeError(f"任务 {task_name} 返回失败状态")
                    logger.warning(
                        "%s-%s 的任务 %s 执行失败并结束，继续重试",
                        account_info.character,
                        account_info.svr,
                        task_name,
                    )
                    continue
                return True
            except RequestHumanTakeover:
                raise
            except Exception as exc:
                self.save_error_log()
                last_exception = exc
                logger.exception(
                    "执行%s失败（%s-%s），第 %s/%s 次：%s",
                    task_name,
                    account_info.character,
                    account_info.svr,
                    attempt,
                    self.task_retry_limit,
                    exc,
                )
            finally:
                if task_success:
                    self._save_private_runtime_record(task_entry, task_config_backup)
                self._restore_private_task_config(task_config_backup)
                self.config._save_selected_fields = previous_save_fields

        task_display_name = self._task_display_name(task_name)
        logger.error(
            f"{account_info.character}-{account_info.svr} 的任务 "
            f"{task_display_name} 连续运行 {self.task_retry_limit} 次失败"
        )
        self._push_error_notification(account_info, task_display_name, last_exception)
        self._restart_game()
        return False

    def _get_task_entry(
        self,
        account_info: MultiAccountRepeatNewAccount,
        task_name: str,
    ) -> MultiAccountRepeatNewTask | None:
        """取得当前账号对应的结构化任务配置。"""
        for entry in account_info.task_list:
            if entry.task_name == task_name:
                return entry
        return None

    @staticmethod
    def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
        """递归套用该账号该任务已经保存的运行记录。"""
        result = copy.deepcopy(base)
        for key, value in override.items():
            if isinstance(value, dict) and isinstance(result.get(key), dict):
                result[key] = ScriptTask._deep_merge(result[key], value)
            else:
                result[key] = copy.deepcopy(value)
        return result

    @classmethod
    def _runtime_diff(cls, before: Any, after: Any, path: tuple[str, ...] = ()) -> Any:
        """保存任务运行中改变的配置字段；公共 scheduler 不属于账号私有记录。"""
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

    def _apply_private_task_config(
        self,
        task_name: str,
        task_entry: MultiAccountRepeatNewTask | MultiAccountRepeatNewFixedTimeBatchTask | None,
    ) -> tuple[str, BaseModel, dict[str, Any]] | None:
        """临时套用当前任务的私有配置和运行记录，并保留完整公共配置供结束后恢复。"""
        if task_entry is None:
            return None
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
        try:
            active = model_with_group_overrides(active, task_entry.private_config)
        except (ValidationError, ValueError) as exc:
            raise ValueError(f"账号私有配置无效：{task_name}: {exc}") from exc

        if task_entry.runtime_record:
            active = active.__class__.model_validate(
                self._deep_merge(active.model_dump(), task_entry.runtime_record)
            )
        BaseModel.__setattr__(self.config.model, task_key, active)
        logger.info(
            "为账号 %s-%s 套用任务 %s 的私有配置和运行记录",
            getattr(getattr(self, "current_account_info", None), "character", ""),
            getattr(getattr(self, "current_account_info", None), "svr", ""),
            task_name,
        )
        return task_key, public_backup, active.model_dump()

    def _save_private_runtime_record(
        self,
        task_entry: MultiAccountRepeatNewTask | MultiAccountRepeatNewFixedTimeBatchTask | None,
        backup_info: tuple[str, BaseModel, dict[str, Any]] | None,
    ) -> None:
        """仅在任务成功时保存本账号本任务的配置型运行记录。"""
        if task_entry is None or backup_info is None:
            return
        task_key, _, baseline = backup_info
        current = getattr(self.config.model, task_key, None)
        if isinstance(current, BaseModel):
            task_entry.runtime_record = self._runtime_diff(baseline, current.model_dump()) or {}

    def _restore_private_task_config(
        self,
        backup_info: tuple[str, BaseModel, dict[str, Any]] | None,
    ) -> None:
        """恢复完整公共配置，避免账号运行记录泄漏到公共配置或其他账号。"""
        if backup_info is None:
            return
        task_key, public_backup, _ = backup_info
        BaseModel.__setattr__(self.config.model, task_key, public_backup)
        self.config.save_selected_fields({task_key: public_backup})

    def _restart_game(self) -> None:
        """重启游戏并等待启动就绪，供任务失败后的恢复流程使用。"""
        logger.info("多账号多任务新：重启游戏以恢复后续执行")
        self.device.app_stop()
        self.device.app_start()
        self.device.wait_app_start_ready()
        # 与单独调度任务一致：进入任务前先走一次正式截图，刷新图片服务帧缓存。
        # 避免启动就绪探测后的页面识别复用重启前已过期的截图帧。
        self.device.screenshot()

    def _is_account_in_scope(self, account_info) -> bool:
        """判断循环任务账号是否属于外层传入的当前账号。"""
        scope = getattr(self, "_account_scope", None)
        if scope is None or account_info is scope:
            return True

        scope_account = getattr(scope, "account", "") or ""
        account_value = getattr(account_info, "account", "") or ""
        if scope_account and account_value:
            return scope_account == account_value

        scope_character = getattr(scope, "character", "") or ""
        scope_svr = getattr(scope, "svr", "") or ""
        if scope_character and scope_svr:
            return (
                account_info.character == scope_character
                and account_info.svr == scope_svr
            )
        if scope_character:
            return account_info.character == scope_character
        return False

    def _switch_account(self, account_info) -> bool:
        """切换到循环任务当前账号。"""
        self._last_switch_error = None
        try:
            return TreeSwitchAccount(
                self.config,
                self.device,
                account_info,
            ).switchAccount()
        except RequestHumanTakeover:
            raise
        except Exception as exc:
            self._last_switch_error = exc
            self.save_error_log()
            logger.exception(
                "切换账号时发生异常（%s-%s）：%s",
                account_info.character,
                account_info.svr,
                exc,
            )
            return False

    @staticmethod
    def _resolve_task_module_path(task_name: str) -> Path:
        """将配置中的内部任务标识解析为实际任务目录，兼容驼峰目录名。"""
        tasks_root = Path.cwd() / "tasks"
        direct_path = tasks_root / task_name / "script_task.py"
        if direct_path.is_file():
            return direct_path

        task_key = convert_to_underscore(task_name)
        candidates = [
            directory / "script_task.py"
            for directory in tasks_root.iterdir()
            if directory.is_dir()
            and convert_to_underscore(directory.name) == task_key
            and (directory / "script_task.py").is_file()
        ]
        if len(candidates) == 1:
            logger.info("内层任务目录解析：%s -> %s", task_name, candidates[0].parent.name)
            return candidates[0]
        if len(candidates) > 1:
            raise ImportError(
                f"任务 {task_name} 对应多个目录：{', '.join(str(path.parent) for path in candidates)}"
            )
        raise ImportError(f"找不到任务 {task_name} 的 script_task.py")

    def create_task_object(self, task_name: str, **kwargs):
        """加载任务，并把当前循环账号传给多账号任务。"""
        current_account_info = kwargs.pop("current_account_info", None)
        module_path = self._resolve_task_module_path(task_name)
        module_name = f"multi_account_repeat_inner_{convert_to_underscore(task_name)}"
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"无法加载任务：{module_path}")

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        task_obj = module.ScriptTask(**kwargs)
        if current_account_info is not None:
            # 只传递当前账号，不设置 current_account_index，避免内层任务把
            # 外层循环账号下标误认为是自己的配置下标。
            task_obj.current_account_info = current_account_info
        return task_obj

