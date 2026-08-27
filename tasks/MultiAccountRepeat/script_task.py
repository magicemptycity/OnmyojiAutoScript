import copy
import importlib.util
import re
from datetime import datetime
from pathlib import Path
from typing import ClassVar

from module.exception import RequestHumanTakeover, TaskEnd
from module.logger import logger
from pydantic import BaseModel, ValidationError
from module.config.utils import convert_to_underscore
from tasks.MultiAccountRepeat.task_name_resolver import TaskNameResolver
from tasks.Component.MultiAccount.account_library import resolve_shared_account
from tasks.Component.MultiAccount.multi_account_priority import MultiAccountPriorityMixin
from tasks.Component.SwitchAccount.assets import SwitchAccountAssets
from tasks.Component.SwitchAccount.switch_account import SwitchAccount
from tasks.GameUi.game_ui import GameUi
from tasks.MultiAccountRepeat.assets import MultiAccountRepeatAssets
from tasks.MultiAccountRepeat.config import (
    MultiAccountRepeat,
    MultiAccountRepeatAccount,
    MultiAccountRepeatTask,
)


class ScriptTask(MultiAccountPriorityMixin, GameUi, MultiAccountRepeatAssets, SwitchAccountAssets):
    """多账号多任务的统一执行器。"""

    task_name: ClassVar[str] = "MultiAccountRepeat"
    multi_account_config_attr: ClassVar[str] = "multi_account_repeat"
    priority_config_attr: ClassVar[str] = "multi_account_repeat_config"
    fade_conf: MultiAccountRepeat = None
    task_retry_limit: ClassVar[int] = 3
    task_display_names: ClassVar[dict[str, str]] = {
        "MultiAccountRepeat": "多账号多任务",
        "MultiAccountRepeatMorning": "多账号多任务上午",
        "MultiAccountRepeatAfternoon": "多账号多任务下午",
        "MultiAccountRepeatMidnight": "多账号多任务凌晨",
        "MultiAccountRepeatDay": "多账号多任务每日",
        "MultiAccountRepeatWeek": "多账号多任务每周",
        "MultiAccountRepeatMonth": "多账号多任务每月",
    }

    def run(self):
        logger.hr(self._current_task_display_name(), 1)
        self.fade_conf = getattr(self.config, self.multi_account_config_attr)
        self._account_scope = getattr(self, "current_account_info", None)
        overall_failed = False

        for account_info in self.fade_conf.account_list:
            self._yield_to_higher_priority_task()
            if not resolve_shared_account(self.config, account_info):
                logger.error("公共账号标识无效：%s", getattr(account_info, "shared_account_identifier", ""))
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
            if (
                self.fade_conf.multi_account_repeat_config.skip_if_logged_today
                and not failed_task_names
                and not unfinished_task_names
            ):
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
            if not task_names:
                logger.warning(
                    "%s-%s 没有配置需要执行的任务",
                    account_info.character,
                    account_info.svr,
                )
                account_info.failed_task_list = ""
                account_info.unfinished_task_list = ""
                self._save_repeat_config()
                continue

            remaining_failed_task_names = list(dict.fromkeys(failed_task_names))
            for task_index, task_name in enumerate(task_names):
                # 先保存当前任务及后续任务；手动停止或强制退出时可从此处继续。
                self._save_unfinished_task_checkpoint(account_info, task_names[task_index:])
                task_entry = self._get_task_entry(account_info, task_name)
                if self._run_task_with_retry(account_info, task_name, task_entry):
                    # 成功的失败重试任务也要立即从失败记录中移除。
                    remaining_failed_task_names = [
                        failed_task
                        for failed_task in remaining_failed_task_names
                        if failed_task != task_name
                    ]
                    account_info.failed_task_list = "\n".join(
                        self._task_display_name(failed_task)
                        for failed_task in remaining_failed_task_names
                    )
                    # 任务成功后立即移除检查点中的当前项，避免在两项任务交接时重复运行。
                    self._save_unfinished_task_checkpoint(account_info, task_names[task_index + 1:])
                    continue
                if task_name not in remaining_failed_task_names:
                    remaining_failed_task_names.append(task_name)
                # 失败任务也立即保存，避免后续任务被手动停止时丢失失败记录。
                account_info.failed_task_list = "\n".join(
                    self._task_display_name(failed_task)
                    for failed_task in remaining_failed_task_names
                )
                self._save_repeat_config()
                overall_failed = True

            # 所有已安排任务都已有明确结果，不再保留中断恢复检查点。
            account_info.unfinished_task_list = ""
            if remaining_failed_task_names:
                account_info.failed_task_list = "\n".join(
                    self._task_display_name(task_name)
                    for task_name in remaining_failed_task_names
                )
                self._save_repeat_config()
                continue

            account_info.failed_task_list = ""
            self.fade_conf.update_account_login_history(account_info)
            self._save_repeat_config()

        incomplete_accounts = self._get_incomplete_accounts_today()
        if (
            self.fade_conf.multi_account_repeat_config.rerun_incomplete_accounts
            and self.fade_conf.multi_account_repeat_config.skip_if_logged_today
            and not getattr(self, "_rerun_incomplete_accounts_done", False)
            and incomplete_accounts
        ):
            logger.warning(
                "本轮结束后仍有账号今天未完成任务，重新执行一次多账号多任务: %s",
                ", ".join(incomplete_accounts),
            )
            # 第二次直接复用完整流程，由“今日已执行则跳过”自动跳过已完成账号。
            self._rerun_incomplete_accounts_done = True
            return self.run()

        self.set_next_run(self.task_name, success=not overall_failed)
        raise TaskEnd(self.task_name)

    @staticmethod
    def _is_account_completed_today(account_info: MultiAccountRepeatAccount) -> bool:
        """根据上次完整完成时间判断账号今天是否已完成。"""
        return account_info.last_complete_time.date() == datetime.now().date()

    def _get_incomplete_accounts_today(self) -> list[str]:
        """返回当前执行范围内今天尚未完整完成任务的有效账号。"""
        accounts = []
        for account_info in self.fade_conf.account_list:
            if not resolve_shared_account(self.config, account_info):
                continue
            if not account_info.is_valid() or not self._is_account_in_scope(account_info):
                continue
            if not self._is_account_completed_today(account_info):
                accounts.append(f"{account_info.character}-{account_info.svr}")
        return accounts

    def _save_repeat_config(self) -> None:
        """只保存当前循环任务状态，避免覆盖页面刚修改的其他配置。"""
        self.config.save_selected_fields({
            self.multi_account_config_attr: self.fade_conf,
        })

    def _save_unfinished_task_checkpoint(
        self,
        account_info: MultiAccountRepeatAccount,
        task_names: list[str],
    ) -> None:
        """在任务开始前持久化未完成任务，供手动停止后的恢复使用。"""
        account_info.unfinished_task_list = "\n".join(
            self._task_display_name(task_name) for task_name in task_names
        )
        self._save_repeat_config()

    def _get_task_names_to_run(
        self,
        account_info: MultiAccountRepeatAccount,
        failed_task_names: list[str],
        unfinished_task_names: list[str],
    ) -> list[str]:
        """优先恢复上次失败或中断的任务，避免重复执行已成功任务。"""
        configured_task_names = account_info.repeat_task_names
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
            account_info.failed_task_list = "\n".join(
                self._task_display_name(task_name)
                for task_name in failed_task_names
                if task_name in configured_task_names
            )
            account_info.unfinished_task_list = "\n".join(
                self._task_display_name(task_name)
                for task_name in unfinished_task_names
                if task_name in configured_task_names
            )
            self._save_repeat_config()

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
        account_info: MultiAccountRepeatAccount,
        task_name: str,
        task_entry: MultiAccountRepeatTask | None = None,
    ) -> bool:
        """执行单个任务；失败后重启游戏并最多重试三次。"""
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
            task_config_backup = self._apply_private_task_config(
                task_name,
                task_entry,
            )
            try:
                task_obj = self.create_task_object(
                    task_name,
                    config=self.config,
                    device=self.device,
                    current_account_info=account_info,
                )
                task_obj.run()
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
                    logger.warning(
                        "%s-%s 的任务 %s 执行失败并结束",
                        account_info.character,
                        account_info.svr,
                        task_name,
                    )
                return task_success
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
        account_info: MultiAccountRepeatAccount,
        task_name: str,
    ) -> MultiAccountRepeatTask | None:
        """取得当前账号对应的结构化任务配置。"""
        for entry in account_info.task_entries:
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

    def _apply_private_task_config(
        self,
        task_name: str,
        task_entry: MultiAccountRepeatTask | None,
    ) -> tuple[str, str, BaseModel, BaseModel] | None:
        """临时套用账号私有配置，并保留公共配置供任务结束后恢复。"""
        if task_entry is None or not task_entry.private_config:
            return None
        task_key = convert_to_underscore(task_name)
        public_config = getattr(self.config.model, task_key, None)
        if not isinstance(public_config, BaseModel):
            raise ValueError(f"找不到任务配置：{task_name}")
        backup = copy.deepcopy(public_config)
        active = copy.deepcopy(public_config)
        for group_name, arguments in task_entry.private_config.items():
            if not isinstance(arguments, dict):
                continue
            for argument_name, value in arguments.items():
                self._set_task_argument(active, group_name, argument_name, value)
        try:
            active = active.__class__.model_validate(active.model_dump())
        except ValidationError as exc:
            raise ValueError(f"账号私有配置无效：{task_name}: {exc}") from exc
        BaseModel.__setattr__(self.config.model, task_key, active)
        logger.info("为账号 %s-%s 套用任务 %s 的私有配置", self.current_account_info.character, self.current_account_info.svr, task_name)
        return task_name, task_key, backup, active

    def _restore_private_task_config(
        self,
        backup_info: tuple[str, str, BaseModel, BaseModel] | None,
    ) -> None:
        """恢复私有参数，保留任务运行期间产生的公共状态变化。"""
        if backup_info is None:
            return
        task_name, task_key, backup, active = backup_info
        current = getattr(self.config.model, task_key, active)
        current = copy.deepcopy(current)
        # 只还原账号私有覆盖过的字段，其余运行状态沿用当前值。
        # 任务项在调用前已绑定到当前账号，恢复时从配置对象中重新读取覆盖路径。
        # 这里通过当前任务记录保存的覆盖路径定位原始值。
        task_entry = self._get_task_entry(self.current_account_info, task_name)
        if task_entry is not None:
            for group_name, arguments in task_entry.private_config.items():
                if not isinstance(arguments, dict):
                    continue
                source_group = self._find_task_group(backup, group_name)
                if source_group is None:
                    continue
                for argument_name in arguments:
                    original = getattr(source_group, convert_to_underscore(argument_name))
                    self._set_task_argument(current, group_name, argument_name, original)
        try:
            current = current.__class__.model_validate(current.model_dump())
        except ValidationError:
            current = backup
        BaseModel.__setattr__(self.config.model, task_key, current)
        self.config.save_selected_fields({task_key: current})

    def _restart_game(self) -> None:
        """重启游戏并等待启动就绪，供任务失败后的恢复流程使用。"""
        logger.info("多账号多任务：重启游戏以恢复后续执行")
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
            return SwitchAccount(
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

    def create_task_object(self, task_name: str, **kwargs):
        """加载任务，并把当前循环账号传给多账号任务。"""
        current_account_info = kwargs.pop("current_account_info", None)
        module_path = Path.cwd() / "tasks" / task_name / "script_task.py"
        module_name = f"multi_account_repeat_inner_{task_name}"
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
