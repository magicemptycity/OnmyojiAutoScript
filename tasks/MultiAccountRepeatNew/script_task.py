import copy
import importlib.util
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar

from module.exception import RequestHumanTakeover, TaskEnd
from module.logger import logger
from pydantic import BaseModel, ValidationError
from module.config.utils import convert_to_underscore
from tasks.MultiAccountRepeatNew.task_name_resolver import TaskNameResolver
from tasks.Restart.server_update import build_server_update_delay_target, is_server_update_window
from tasks.Component.MultiAccount.multi_account_priority import MultiAccountPriorityMixin
from tasks.Component.SwitchAccount.assets import SwitchAccountAssets
from tasks.Component.SwitchAccount.tree_switch_account import TreeSwitchAccount
from tasks.GameUi.game_ui import GameUi
from tasks.MultiAccountRepeatNew.assets import MultiAccountRepeatNewAssets
from tasks.MultiAccountRepeatNew.config import (
    MultiAccountRepeatNew,
    MultiAccountRepeatNewAccount,
    MultiAccountRepeatNewFixedTimeBatch,
    MultiAccountRepeatNewFixedTimeBatchProgress,
    MultiAccountRepeatNewFixedTimeBatchTask,
    MultiAccountRepeatNewTask,
)


class ScriptTask(MultiAccountPriorityMixin, GameUi, MultiAccountRepeatNewAssets, SwitchAccountAssets):
    """多账号多任务新的独立执行器。"""

    task_name: ClassVar[str] = "MultiAccountRepeatNew"
    multi_account_config_attr: ClassVar[str] = "multi_account_repeat_new"
    priority_config_attr: ClassVar[str] = "multi_account_repeat_new_config"
    fade_conf: MultiAccountRepeatNew = None
    task_retry_limit: ClassVar[int] = 3
    task_display_names: ClassVar[dict[str, str]] = {
        "MultiAccountRepeatNew": "多账号多任务新",
    }

    def run(self):
        logger.hr(self._current_task_display_name(), 1)
        self.fade_conf = getattr(self.config, self.multi_account_config_attr)
        self._account_scope = getattr(self, "current_account_info", None)
        # 普通列表和混合版进入账号前统一拦截维护时间，避免切号后才发现停服。
        if self._delay_for_server_update_before_accounts():
            raise TaskEnd(self.task_name)
        if self._has_fixed_time_batches():
            return self._run_fixed_time_batches()
        overall_failed = False

        for account_info in self.fade_conf.account_list:
            self._yield_to_higher_priority_task()
            if not self._sync_account_from_public(account_info):
                logger.error("多账号多任务新公共账号不存在：%s", account_info.public_account_identifier)
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
                "本轮结束后仍有账号今天未完成任务，重新执行一次多账号多任务新: %s",
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

    def _has_fixed_time_batches(self) -> bool:
        """任一账号存在启用且含任务的固定时间批次时，使用批次调度模式。"""
        return any(
            batch.enable and batch.task_names
            for account in self.fade_conf.account_list
            for batch in account.fixed_time_batch_list
        )

    @staticmethod
    def _enabled_fixed_time_batches(
        account_info: MultiAccountRepeatNewAccount,
    ) -> list[MultiAccountRepeatNewFixedTimeBatch]:
        return [
            batch for batch in account_info.fixed_time_batch_list
            if batch.enable and batch.task_names
        ]

    @staticmethod
    def _is_fixed_batch_scheduled_on(
        batch: MultiAccountRepeatNewFixedTimeBatch,
        target_date,
    ) -> bool:
        """判断批次在指定日期是否应运行。"""
        mode = getattr(batch, "schedule_mode", "daily")
        if mode == "weekday":
            weekdays = set(getattr(batch, "weekdays", []) or [])
            return target_date.isoweekday() in weekdays
        if mode == "interval":
            last_run_time = getattr(batch, "last_run_time", datetime(2023, 1, 1))
            return target_date >= last_run_time.date() + timedelta(days=max(1, batch.interval_days))
        return True

    @classmethod
    def _is_fixed_batch_due(
        cls,
        batch: MultiAccountRepeatNewFixedTimeBatch,
        now: datetime,
    ) -> bool:
        return (
            batch.run_time <= now.time()
            and cls._is_fixed_batch_scheduled_on(batch, now.date())
        )

    @classmethod
    def _next_fixed_batch_target(
        cls,
        batch: MultiAccountRepeatNewFixedTimeBatch,
        now: datetime,
        *,
        allow_due_now: bool = False,
    ) -> datetime | None:
        """按批次周期寻找下一次时间；配置刚修改时允许已过点的当日批次立即执行。"""
        for offset in range(367):
            target_date = now.date() + timedelta(days=offset)
            if not cls._is_fixed_batch_scheduled_on(batch, target_date):
                continue
            target = datetime.combine(target_date, batch.run_time)
            if target > now or (allow_due_now and offset == 0):
                return target
        return None

    @staticmethod
    def _batch_progress_for_today(
        account_info: MultiAccountRepeatNewAccount,
        batch: MultiAccountRepeatNewFixedTimeBatch,
    ) -> MultiAccountRepeatNewFixedTimeBatchProgress:
        progress = account_info.fixed_time_batch_progress.get(batch.batch_id)
        if progress is None or progress.progress_time.date() != datetime.now().date():
            return MultiAccountRepeatNewFixedTimeBatchProgress()
        return progress

    def _fixed_batch_task_names_to_run(
        self,
        account_info: MultiAccountRepeatNewAccount,
        batch: MultiAccountRepeatNewFixedTimeBatch,
    ) -> list[str]:
        """按账号和批次恢复失败/中断项，已完成项不重复运行。"""
        configured = batch.task_names
        progress = self._batch_progress_for_today(account_info, batch)
        completed = set(progress.completed_task_names)
        recovery = set(progress.failed_task_names) | set(progress.unfinished_task_names)
        if recovery:
            return [name for name in configured if name in recovery]
        return [name for name in configured if name not in completed]

    def _save_fixed_batch_progress(
        self,
        account_info: MultiAccountRepeatNewAccount,
        batch: MultiAccountRepeatNewFixedTimeBatch,
        completed_task_names: list[str],
        failed_task_names: list[str],
        unfinished_task_names: list[str],
    ) -> None:
        """保存单账号单批次检查点，并刷新任务列表中的今日状态。"""
        account_info.fixed_time_batch_progress[batch.batch_id] = MultiAccountRepeatNewFixedTimeBatchProgress(
            progress_time=datetime.now(),
            completed_task_list="\n".join(self._task_display_name(name) for name in dict.fromkeys(completed_task_names)),
            failed_task_list="\n".join(self._task_display_name(name) for name in dict.fromkeys(failed_task_names)),
            unfinished_task_list="\n".join(self._task_display_name(name) for name in dict.fromkeys(unfinished_task_names)),
        )
        self._refresh_fixed_batch_task_status(account_info)
        self._save_repeat_config()

    def _refresh_fixed_batch_task_status(self, account_info: MultiAccountRepeatNewAccount) -> None:
        """将各批次当天状态汇总到账号普通任务列表，供旧页面状态标签使用。"""
        completed: set[str] = set()
        failed: set[str] = set()
        unfinished: set[str] = set()
        today = datetime.now().date()
        for progress in account_info.fixed_time_batch_progress.values():
            if progress.progress_time.date() != today:
                continue
            completed.update(progress.completed_task_names)
            failed.update(progress.failed_task_names)
            unfinished.update(progress.unfinished_task_names)
        unfinished -= failed
        completed -= failed | unfinished
        account_info.completed_task_list = "\n".join(
            self._task_display_name(name) for name in account_info.task_names if name in completed
        )
        account_info.failed_task_list = "\n".join(
            self._task_display_name(name) for name in account_info.task_names if name in failed
        )
        account_info.unfinished_task_list = "\n".join(
            self._task_display_name(name) for name in account_info.task_names if name in unfinished
        )
        account_info.task_progress_time = datetime.now()

    def _run_fixed_time_batches(self):
        """到点后按各账号自己的固定时间批次运行；同一账号在一轮中只切号一次。"""
        now = datetime.now()
        overall_failed = False
        # 新固定时间版会直接调用本方法，因此这里也必须独立拦截维护时间。
        if self._delay_for_server_update_before_accounts():
            raise TaskEnd(self.task_name)
        for account_info in self.fade_conf.account_list:
            self._yield_to_higher_priority_task()
            if not self._sync_account_from_public(account_info):
                logger.error("多账号多任务新公共账号不存在：%s", account_info.public_account_identifier)
                overall_failed = True
                continue
            if not account_info.is_valid() or not self._is_account_in_scope(account_info):
                continue
            account_batches = [
                (batch, self._fixed_batch_task_names_to_run(account_info, batch))
                for batch in self._enabled_fixed_time_batches(account_info)
                if self._is_fixed_batch_due(batch, now)
            ]
            account_batches = [item for item in account_batches if item[1]]
            if not account_batches:
                continue
            logger.hr(f"处理账号 {account_info.character}-{account_info.svr}", 2)
            logger.info("开始处理账号 %s-%s 的固定时间批次", account_info.character, account_info.svr)
            if not self._switch_account(account_info):
                overall_failed = True
                self._push_error_notification(account_info, "切换账号", getattr(self, "_last_switch_error", None))
                continue
            for batch, task_names in account_batches:
                if not self._run_fixed_time_batch(account_info, batch, task_names):
                    overall_failed = True
        # 固定时间批次只由各账号自己的批次时间决定，不受成功/失败间隔和服务器更新时间改写。
        self.set_next_run(
            self.task_name,
            success=None,
            server=False,
            target=self._next_fixed_batch_run(now),
        )
        raise TaskEnd(self.task_name)

    def _run_fixed_time_batch(
        self,
        account_info: MultiAccountRepeatNewAccount,
        batch: MultiAccountRepeatNewFixedTimeBatch,
        task_names: list[str],
    ) -> bool:
        """运行一个账号的一项固定时间批次，并在每项任务前后持久化检查点。"""
        logger.hr(f"{account_info.character}-{account_info.svr} 固定时间批次 {batch.run_time.strftime('%H:%M')}", 3)
        progress = self._batch_progress_for_today(account_info, batch)
        completed = list(dict.fromkeys(progress.completed_task_names))
        failed = list(dict.fromkeys(progress.failed_task_names))
        overall_failed = False
        for index, task_name in enumerate(task_names):
            self._save_fixed_batch_progress(account_info, batch, completed, failed, task_names[index:])
            entry = batch.task_entry(task_name)
            if self._run_task_with_retry(account_info, task_name, entry):
                failed = [name for name in failed if name != task_name]
                if task_name not in completed:
                    completed.append(task_name)
                self._save_fixed_batch_progress(account_info, batch, completed, failed, task_names[index + 1:])
                continue
            if task_name not in failed:
                failed.append(task_name)
            completed = [name for name in completed if name != task_name]
            self._save_fixed_batch_progress(account_info, batch, completed, failed, task_names[index + 1:])
            overall_failed = True
        if not overall_failed:
            # 只有整个批次成功后才推进间隔天数，失败任务仍可在当前计划日恢复执行。
            batch.last_run_time = datetime.now()
            self._save_fixed_batch_progress(account_info, batch, completed, [], [])
        return not overall_failed

    def _next_fixed_batch_run(self, now: datetime | None = None) -> datetime | None:
        """返回所有账号中下一项固定时间批次；已过点的批次安排到次日。"""
        now = now or datetime.now()
        targets = []
        for account_info in self.fade_conf.account_list:
            if not self._is_account_in_scope(account_info):
                continue
            for batch in self._enabled_fixed_time_batches(account_info):
                target = self._next_fixed_batch_target(batch, now)
                if target is not None:
                    targets.append(target)
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
    def _find_task_group(task_config: BaseModel, group_name: str) -> Any:
        """按照参数页面使用的分组名称找到普通分组或列表分组。"""
        group_name = convert_to_underscore(group_name)
        group = getattr(task_config, group_name, None)
        if group is not None:
            return group
        matches = re.findall(r"\d+", group_name)
        index = int(matches[-1]) - 1 if matches else -1
        if index < 0:
            return None
        for field_name, value in task_config.__dict__.items():
            if field_name in group_name and isinstance(value, list) and index < len(value):
                return value[index]
        return None

    @classmethod
    def _set_task_argument(
        cls,
        task_config: BaseModel,
        group_name: str,
        argument_name: str,
        value: Any,
    ) -> None:
        group = cls._find_task_group(task_config, group_name)
        if group is None:
            raise ValueError(f"找不到任务参数分组：{group_name}")
        setattr(group, convert_to_underscore(argument_name), value)

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
        for group_name, arguments in task_entry.private_config.items():
            if not isinstance(arguments, dict):
                continue
            for argument_name, value in arguments.items():
                self._set_task_argument(active, group_name, argument_name, value)
        try:
            active = active.__class__.model_validate(active.model_dump())
        except ValidationError as exc:
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

