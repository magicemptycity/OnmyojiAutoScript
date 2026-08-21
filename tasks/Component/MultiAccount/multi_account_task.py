"""多账号任务共用的外层轮询执行器。"""

import importlib.util
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar

from module.exception import (
    GameNotRunningError,
    GamePageUnknownError,
    GameStuckError,
    GameTooManyClickError,
    RequestHumanTakeover,
    TaskEnd,
)
from module.logger import logger
from tasks.Component.MultiAccount.account_library import resolve_shared_account
from tasks.Component.SwitchAccount.assets import SwitchAccountAssets
from tasks.Component.SwitchAccount.switch_account import SwitchAccount
from tasks.GameUi.game_ui import GameUi


class MultiAccountTaskBase(GameUi, SwitchAccountAssets):
    """多账号任务的通用调度基类。

    子类只需要实现账号筛选、账号执行和账号级状态更新，公共的切号、异常隔离、
    内层任务上下文传递、逐账号保存以及最终调度逻辑由这里统一处理。
    """

    task_name: ClassVar[str] = ""
    multi_account_config_attr: ClassVar[str] = ""
    retry_delay: ClassVar[timedelta] = timedelta(minutes=10)
    server_schedule: ClassVar[bool] = True

    fade_conf: Any = None
    current_account_index: int | None = None
    task_display_names: ClassVar[dict[str, str]] = {
        "MultiAccountKekkaiUtilize": "多账号蹭卡",
        "MultiAccountKekkaiActivation": "多账号挂卡",
        "MultiAccountDelegation": "多账号式神委派",
        "MultiAccountAreaBoss": "多账号地域鬼王",
        "MultiAccountHunt": "多账号狩猎战",
    }
    inner_task_display_name: ClassVar[str] = ""
    current_account_info: Any = None
    current_account_config: Any = None

    def run(self):
        self.fade_conf = self.get_multi_account_config()
        # 多账号任务被“多账号多任务”调用时，只处理外层传入的当前账号。
        self._account_scope = self._get_account_scope()
        self._invalid_shared_account_ids = {
            id(account)
            for account in self.fade_conf.account_list
            if not resolve_shared_account(self.config, account)
        }
        for account in self.fade_conf.account_list:
            if id(account) in self._invalid_shared_account_ids:
                logger.error("公共账号序号无效：%s", getattr(account, "shared_account_index", 0))
        pending_accounts = self.collect_pending_accounts(datetime.now())
        pending_accounts = self._filter_scoped_accounts(pending_accounts)

        if not pending_accounts:
            self.save_multi_account_config()
            self.set_next_run(
                self.task_name,
                success=True,
                server=self.server_schedule,
                target=self.get_next_run_time(),
            )
            raise TaskEnd(self.task_name)

        overall_failed = False
        nested_exception: Exception | None = None
        for index, account in pending_accounts:
            self._set_account_context(index, account)
            logger.info("开始处理账号 %s-%s", account.character, account.svr)

            if not self._switch_account(account):
                overall_failed = True
                logger.warning("切换到账号 %s-%s 失败", account.character, account.svr)
                if not self._is_nested_multi_account_task():
                    self._push_error_notification(
                        account,
                        "切换账号",
                        getattr(self, "_last_switch_error", None),
                    )
                nested_exception = getattr(self, "_last_switch_error", None)
                self.on_account_failure(index, account, "切换账号失败")
                self.save_multi_account_config()
                self._clear_account_context()
                continue

            account_success = False
            try:
                self.prepare_account(index, account)
                result = self.run_account(index, account)
                account_success = result is not False
            except TaskEnd:
                # 内层任务通常通过 TaskEnd 表示流程正常结束。
                account_success = True
            except RequestHumanTakeover:
                raise
            except (GameNotRunningError, GamePageUnknownError, GameStuckError, GameTooManyClickError):
                # 环境异常必须上抛给外层调度器；多账号多任务会据此重启游戏并重试当前任务。
                raise
            except Exception as exc:
                self.save_error_log()
                nested_exception = exc
                overall_failed = True
                logger.exception(
                    "执行%s失败（%s-%s）：%s",
                    self.task_name,
                    account.character,
                    account.svr,
                    exc,
                )
                if not self._is_nested_multi_account_task():
                    self._push_error_notification(account, self._failed_task_display_name(), exc)
            finally:
                try:
                    self.cleanup_account(index, account, account_success)
                except RequestHumanTakeover:
                    raise
                except (GameNotRunningError, GamePageUnknownError, GameStuckError, GameTooManyClickError):
                    # 清理阶段出现环境异常同样交给外层恢复，不能带着异常页面继续下一个账号。
                    raise
                except Exception as exc:
                    self.save_error_log()
                    nested_exception = exc
                    account_success = False
                    overall_failed = True
                    logger.exception(
                        "清理%s账号上下文失败（%s-%s）：%s",
                        self.task_name,
                        account.character,
                        account.svr,
                        exc,
                    )
                    if not self._is_nested_multi_account_task():
                        self._push_error_notification(account, "清理账号上下文", exc)

            if account_success:
                try:
                    success_result = self.on_account_success(index, account)
                except RequestHumanTakeover:
                    raise
                except Exception as exc:
                    self.save_error_log()
                    nested_exception = exc
                    success_result = False
                    logger.exception(
                        "更新%s账号状态失败（%s-%s）：%s",
                        self.task_name,
                        account.character,
                        account.svr,
                        exc,
                    )
                    if not self._is_nested_multi_account_task():
                        self._push_error_notification(account, "更新账号状态", exc)
                if success_result is False:
                    account_success = False
                    overall_failed = True

            if not account_success:
                overall_failed = True
                self.on_account_failure(index, account, "账号任务执行失败")

            self.save_multi_account_config()
            self._clear_account_context()

        self.set_next_run(
            self.task_name,
            success=not overall_failed,
            server=self.server_schedule,
            target=self.get_next_run_time(),
        )
        if overall_failed and self._is_nested_multi_account_task():
            # 被多账号多任务调用时，必须让外层识别失败并执行其三次重启重试。
            # 有原始异常时保留其类型，便于外层通知准确显示报错类型。
            if nested_exception is not None:
                raise nested_exception
            raise RuntimeError(f"{self._task_display_name()}当前账号执行失败")
        raise TaskEnd(self.task_name)

    def _is_nested_multi_account_task(self) -> bool:
        """判断当前是否由多账号多任务传入单个账号范围调用。"""
        return getattr(self, "_account_scope", None) is not None

    def _failed_task_display_name(self) -> str:
        return self.inner_task_display_name or self._task_display_name()

    def _push_error_notification(self, account: Any, failed_task: str, exc: Exception | None = None) -> None:
        account_name = f"{account.character}-{account.svr}"
        error_type = type(exc).__name__ if exc is not None else "切换失败"
        current_task = self._task_display_name()
        self.config.notifier.push(
            title=f"{current_task}报错：{account_name}",
            content=(
                f"当前功能：{current_task}\n"
                f"报错账号：{account_name}\n"
                f"报错功能：{failed_task}\n"
                f"报错类型：{error_type}"
            ),
        )

    def _task_display_name(self) -> str:
        """返回当前多账号功能用于通知的中文名称。"""
        return self.task_display_names.get(self.task_name, self.task_name)

    def get_multi_account_config(self) -> Any:
        """获取外层多账号配置对象。"""
        config = getattr(self.config, self.multi_account_config_attr, None)
        if config is None:
            config = getattr(self.config.model, self.multi_account_config_attr)
        return config

    def collect_pending_accounts(self, now: datetime) -> list[tuple[int, Any]]:
        """返回本轮需要执行的账号及其下标。"""
        return [
            (index, account)
            for index, account in enumerate(self.fade_conf.account_list)
            if id(account) not in getattr(self, "_invalid_shared_account_ids", set())
            and account.is_valid() and self.should_run_account(index, account, now)
        ]

    def should_run_account(self, index: int, account: Any, now: datetime) -> bool:
        """判断账号是否应该在本轮执行。"""
        return self._is_account_in_scope(account)

    def get_account_config(self, index: int, account: Any) -> Any:
        """返回当前账号对应的私有配置，默认没有私有配置。"""
        return None

    def prepare_account(self, index: int, account: Any) -> None:
        """账号切换成功后、执行内层任务前的准备钩子。"""

    def run_account(self, index: int, account: Any) -> bool | None:
        """执行一个账号的实际业务。"""
        raise NotImplementedError

    def cleanup_account(self, index: int, account: Any, account_success: bool) -> None:
        """账号业务结束后的清理钩子。"""

    def on_account_success(self, index: int, account: Any) -> bool | None:
        """账号执行成功后的状态钩子。"""
        account.last_complete_time = datetime.now()
        return True

    def on_account_failure(self, index: int, account: Any, reason: str) -> None:
        """账号执行失败后的状态钩子。"""

    def get_next_run_time(self) -> datetime | None:
        """返回仍在未来的外层计划时间，子类可按账号状态覆盖。"""
        scheduler = getattr(self.fade_conf, "scheduler", None)
        next_run = getattr(scheduler, "next_run", None) if scheduler else None
        if isinstance(next_run, datetime) and next_run > datetime.now():
            return next_run
        # 已到期的旧调度时间不能再作为 target 传回 task_delay，
        # 否则会覆盖成功/失败间隔，导致任务立即循环运行。
        return None

    def _get_account_scope(self) -> Any:
        """读取外层循环任务传入的当前账号。"""
        if getattr(self, "current_account_index", None) is not None:
            return None
        return getattr(self, "current_account_info", None)

    def _is_account_in_scope(self, account: Any) -> bool:
        """判断账号是否属于当前执行范围。"""
        scope = getattr(self, "_account_scope", None)
        if scope is None:
            return True
        if account is scope:
            return True

        scope_account = getattr(scope, "account", "") or ""
        account_value = getattr(account, "account", "") or ""
        if scope_account and account_value:
            return scope_account == account_value

        scope_character = getattr(scope, "character", "") or ""
        scope_svr = getattr(scope, "svr", "") or ""
        if scope_character and scope_svr:
            return (
                account.character == scope_character
                and account.svr == scope_svr
            )
        if scope_character:
            return account.character == scope_character
        return False

    def _filter_scoped_accounts(self, accounts: list[tuple[int, Any]]) -> list[tuple[int, Any]]:
        """过滤当前账号范围，防止嵌套多账号任务再次遍历全部账号。"""
        if getattr(self, "_account_scope", None) is None:
            return accounts
        return [
            (index, account)
            for index, account in accounts
            if self._is_account_in_scope(account)
        ]

    def get_scoped_accounts(self) -> list[Any]:
        """返回当前执行范围内的账号，供子类计算下次调度时间。"""
        return [
            account
            for account in self.fade_conf.account_list
            if self._is_account_in_scope(account)
        ]

    def save_multi_account_config(self) -> None:
        """只保存当前多账号任务状态，避免覆盖页面刚修改的其他配置。"""
        self.config.save_selected_fields({
            self.multi_account_config_attr: self.fade_conf,
        })

    def create_task_object(
        self,
        task_name: str,
        *,
        script_name: str = "script_task.py",
        **kwargs,
    ):
        """加载一个内层任务，并传递当前账号上下文。"""
        module_path = Path.cwd() / "tasks" / task_name / script_name
        module_name = f"multi_account_inner_{task_name}_{module_path.stem}"
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"无法加载内层任务：{module_path}")

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        task_obj = module.ScriptTask(**kwargs)
        self.attach_account_context(task_obj)
        return task_obj

    def attach_account_context(self, task_obj: Any) -> Any:
        """把当前账号信息和私有配置传递给内层任务。"""
        if isinstance(task_obj, MultiAccountTaskBase):
            # 嵌套多账号任务必须根据当前账号重新查找自己的配置下标。
            task_obj.current_account_index = None
        else:
            task_obj.current_account_index = self.current_account_index
        task_obj.current_account_info = self.current_account_info
        task_obj.current_account_config = self.current_account_config
        return task_obj

    def _switch_account(self, account: Any) -> bool:
        # 多账号多任务已成功切到当前账号，内层多账号任务无需重复切号。
        if getattr(self, "_account_scope", None) is not None and self._is_account_in_scope(account):
            logger.info("多账号多任务已切换到当前账号，跳过重复切号")
            return True

        self._last_switch_error = None
        try:
            return SwitchAccount(self.config, self.device, account).switchAccount()
        except (
            GameNotRunningError,
            GamePageUnknownError,
            GameStuckError,
            GameTooManyClickError,
            RequestHumanTakeover,
        ):
            # 游戏环境异常必须交给全局恢复流程重启。
            raise
        except Exception as exc:
            self._last_switch_error = exc
            self.save_error_log()
            logger.exception(
                "切换账号时发生异常（%s-%s）：%s",
                account.character,
                account.svr,
                exc,
            )
            return False

    def _set_account_context(self, index: int, account: Any) -> None:
        self.current_account_index = index
        self.current_account_info = account
        self.current_account_config = self.get_account_config(index, account)

    def _clear_account_context(self) -> None:
        self.current_account_index = None
        self.current_account_info = None
        self.current_account_config = None
