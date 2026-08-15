import importlib.util
from datetime import datetime
from pathlib import Path
from typing import ClassVar

from module.exception import RequestHumanTakeover, TaskEnd
from module.logger import logger
from tasks.MultiAccountRepeat.task_name_resolver import TaskNameResolver
from tasks.Component.MultiAccount.account_library import resolve_shared_account
from tasks.Component.SwitchAccount.assets import SwitchAccountAssets
from tasks.Component.SwitchAccount.switch_account import SwitchAccount
from tasks.GameUi.game_ui import GameUi
from tasks.MultiAccountRepeat.assets import MultiAccountRepeatAssets
from tasks.MultiAccountRepeat.config import (
    MultiAccountRepeat,
    MultiAccountRepeatAccount,
)


class ScriptTask(GameUi, MultiAccountRepeatAssets, SwitchAccountAssets):
    """多账号多任务的统一执行器。"""

    task_name: ClassVar[str] = "MultiAccountRepeat"
    multi_account_config_attr: ClassVar[str] = "multi_account_repeat"
    fade_conf: MultiAccountRepeat = None
    task_retry_limit: ClassVar[int] = 3
    task_display_names: ClassVar[dict[str, str]] = {
        "MultiAccountRepeat": "多账号多任务",
        "MultiAccountRepeatMorning": "多账号多任务上午",
        "MultiAccountRepeatAfternoon": "多账号多任务下午",
        "MultiAccountRepeatDay": "多账号多任务每日",
        "MultiAccountRepeatWeek": "多账号多任务每周",
        "MultiAccountRepeatMonth": "多账号多任务每月",
    }

    def run(self):
        self.fade_conf = getattr(self.config, self.multi_account_config_attr)
        self._account_scope = getattr(self, "current_account_info", None)
        overall_failed = False

        for account_info in self.fade_conf.account_list:
            if not resolve_shared_account(self.config, account_info):
                logger.error("公共账号序号无效：%s", getattr(account_info, "shared_account_index", 0))
                overall_failed = True
                continue
            if not account_info.is_valid():
                continue
            if not self._is_account_in_scope(account_info):
                continue
            logger.info("开始处理账号 %s-%s", account_info.character, account_info.svr)

            failed_task_names = account_info.failed_task_names
            if (
                self.fade_conf.multi_account_repeat_config.skip_if_logged_today
                and not failed_task_names
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

            task_names = self._get_task_names_to_run(account_info, failed_task_names)
            if not task_names:
                logger.warning(
                    "%s-%s 没有配置需要执行的任务",
                    account_info.character,
                    account_info.svr,
                )
                account_info.failed_task_list = ""
                setattr(self.config.model, self.multi_account_config_attr, self.fade_conf)
                self.config.save()
                continue

            current_failed_task_names = []
            for task_name in task_names:
                if self._run_task_with_retry(account_info, task_name):
                    continue
                current_failed_task_names.append(task_name)
                overall_failed = True

            if current_failed_task_names:
                account_info.failed_task_list = "\n".join(
                    self._task_display_name(task_name)
                    for task_name in current_failed_task_names
                )
                setattr(self.config.model, self.multi_account_config_attr, self.fade_conf)
                self.config.save()
                continue

            account_info.failed_task_list = ""
            self.fade_conf.update_account_login_history(account_info)
            setattr(self.config.model, self.multi_account_config_attr, self.fade_conf)
            self.config.save()

        self.set_next_run(self.task_name, success=not overall_failed)
        raise TaskEnd(self.task_name)

    def _get_task_names_to_run(
        self,
        account_info: MultiAccountRepeatAccount,
        failed_task_names: list[str],
    ) -> list[str]:
        """优先返回上次运行失败的任务，避免重复执行已成功任务。"""
        configured_task_names = account_info.repeat_task_names
        if not failed_task_names:
            return configured_task_names

        task_names = [
            task_name
            for task_name in failed_task_names
            if task_name in configured_task_names
        ]
        removed_task_names = [
            task_name
            for task_name in failed_task_names
            if task_name not in configured_task_names
        ]
        if removed_task_names:
            logger.warning(
                "%s-%s 失败任务已不在当前任务列表中，清除记录: %s",
                account_info.character,
                account_info.svr,
                ", ".join(removed_task_names),
            )
            account_info.failed_task_list = "\n".join(
                self._task_display_name(task_name) for task_name in task_names
            )

        if task_names:
            logger.info(
                "%s-%s 上次失败任务，本次仅重试: %s",
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
                logger.info(
                    "%s-%s 的任务 %s 执行结束",
                    account_info.character,
                    account_info.svr,
                    task_name,
                )
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

        task_display_name = self._task_display_name(task_name)
        logger.error(
            f"{account_info.character}-{account_info.svr} 的任务 "
            f"{task_display_name} 连续运行 {self.task_retry_limit} 次失败"
        )
        self._push_error_notification(account_info, task_display_name, last_exception)
        self._restart_game()
        return False

    def _restart_game(self) -> None:
        """重启游戏并等待启动就绪，供任务失败后的恢复流程使用。"""
        logger.info("多账号多任务：重启游戏以恢复后续执行")
        self.device.app_stop()
        self.device.app_start()
        self.device.wait_app_start_ready()

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
