import copy
from typing import Any

from tasks.Component.MultiAccount.multi_account_task import MultiAccountTaskBase
from tasks.MultiAccountAreaBoss.assets import MultiAccountAreaBossAssets
from tasks.MultiAccountAreaBoss.config import (
    MultiAccountAreaBoss,
    MultiAccountAreaBossAccount,
)


class ScriptTask(MultiAccountTaskBase, MultiAccountAreaBossAssets):
    """多账号地域鬼王的外层调度器。"""

    task_name = "MultiAccountAreaBoss"
    multi_account_config_attr = "multi_account_area_boss"
    inner_task_display_name = "地域鬼王"
    fade_conf: MultiAccountAreaBoss = None

    def get_account_config(
        self,
        index: int,
        account: MultiAccountAreaBossAccount,
    ) -> Any:
        """按账号下标选择私有配置，未启用时返回公共配置。"""
        if account.enable_private_config and index < len(self.fade_conf.private_config):
            return self.fade_conf.private_config[index]
        return self.fade_conf.common_config

    def prepare_account(self, index: int, account: MultiAccountAreaBossAccount) -> None:
        """备份内层地域鬼王配置，避免账号之间互相污染。"""
        self._area_boss_config_backup = copy.deepcopy(self.config.area_boss)

    def run_account(self, index: int, account: MultiAccountAreaBossAccount) -> bool | None:
        """把当前账号配置传给内层地域鬼王任务并执行。"""
        task_obj = self.create_task_object(
            "MultiAccountAreaBoss",
            script_name="area_script_task.py",
            config=self.config,
            device=self.device,
        )
        task_obj.run()
        return True

    def cleanup_account(
        self,
        index: int,
        account: MultiAccountAreaBossAccount,
        account_success: bool,
    ) -> None:
        """恢复内层配置，保证下一个账号从原始配置开始。"""
        latest_outer_config = getattr(self.config.model, self.multi_account_config_attr, None)
        latest_scheduler = getattr(latest_outer_config, "scheduler", None)
        if latest_scheduler is not None:
            self.fade_conf.scheduler = copy.deepcopy(latest_scheduler)

        backup = getattr(self, "_area_boss_config_backup", None)
        if backup is None:
            return
        self.config.model.area_boss = backup
        del self._area_boss_config_backup

    def create_area_boss_task(
        self,
        account_info: MultiAccountAreaBossAccount,
        **kwargs,
    ):
        """保留旧接口，方便其他代码继续创建内层任务。"""
        kwargs.setdefault("config", self.config)
        kwargs.setdefault("device", self.device)
        task_obj = self.create_task_object(
            "MultiAccountAreaBoss",
            script_name="area_script_task.py",
            **kwargs,
        )
        task_obj.current_account_info = account_info
        index = self.current_account_index
        if index is None:
            index = next(
                (
                    item_index
                    for item_index, item in enumerate(self.fade_conf.account_list)
                    if item is account_info
                ),
                -1,
            )
        if index >= 0:
            task_obj.current_account_config = self.get_account_config(index, account_info)
        return task_obj
