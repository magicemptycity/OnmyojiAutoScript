import importlib.util
from datetime import datetime
from pathlib import Path
from typing import ClassVar

from module.exception import RequestHumanTakeover, TaskEnd
from module.logger import logger
from tasks.Component.SwitchAccount.assets import SwitchAccountAssets
from tasks.Component.SwitchAccount.switch_account import SwitchAccount
from tasks.GameUi.game_ui import GameUi
from tasks.MultiAccountRepeat.assets import MultiAccountRepeatAssets
from tasks.MultiAccountRepeat.config import MultiAccountRepeat


class ScriptTask(GameUi, MultiAccountRepeatAssets, SwitchAccountAssets):
    """多账号多任务的统一执行器。"""

    task_name: ClassVar[str] = "MultiAccountRepeat"
    multi_account_config_attr: ClassVar[str] = "multi_account_repeat"
    fade_conf: MultiAccountRepeat = None

    def run(self):
        self.fade_conf = getattr(self.config, self.multi_account_config_attr)
        self._account_scope = getattr(self, "current_account_info", None)
        overall_failed = False

        for account_info in self.fade_conf.account_list:
            if not account_info.is_valid():
                continue
            if not self._is_account_in_scope(account_info):
                continue
            logger.info("开始处理账号 %s-%s", account_info.character, account_info.svr)

            if self.fade_conf.multi_account_repeat_config.skip_if_logged_today:
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
                continue

            task_names = account_info.repeat_task_names
            if not task_names:
                logger.warning(
                    "%s-%s 没有配置需要执行的任务",
                    account_info.character,
                    account_info.svr,
                )
                continue

            task_failed = False
            for task_name in task_names:
                logger.info(
                    "为账号 %s-%s 执行多账号多任务项 %s",
                    account_info.character,
                    account_info.svr,
                    task_name,
                )
                try:
                    task_obj = self.create_task_object(
                        task_name,
                        config=self.config,
                        device=self.device,
                        current_account_info=account_info,
                    )
                    task_obj.run()
                except TaskEnd:
                    logger.info(
                        "%s-%s 的任务 %s 执行结束",
                        account_info.character,
                        account_info.svr,
                        task_name,
                    )
                except RequestHumanTakeover:
                    raise
                except Exception as exc:
                    logger.exception(
                        "执行%s失败（%s-%s）：%s",
                        task_name,
                        account_info.character,
                        account_info.svr,
                        exc,
                    )
                    task_failed = True
                    overall_failed = True

            if task_failed:
                continue

            self.fade_conf.update_account_login_history(account_info)
            setattr(self.config.model, self.multi_account_config_attr, self.fade_conf)
            self.config.save()

        self.set_next_run(self.task_name, success=not overall_failed)
        raise TaskEnd(self.task_name)

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
        try:
            return SwitchAccount(
                self.config,
                self.device,
                account_info,
            ).switchAccount()
        except RequestHumanTakeover:
            raise
        except Exception as exc:
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
