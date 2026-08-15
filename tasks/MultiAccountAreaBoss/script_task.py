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
        """备份原地域鬼王配置，并映射当前账号配置。"""
        self._area_boss_config_backup = copy.deepcopy(self.config.area_boss)
        active_config = self.get_account_config(index, account)

        boss_config = self.config.area_boss.boss
        boss_config.boss_number = active_config.boss_number
        boss_config.boss_reward = active_config.boss_reward
        boss_config.reward_floor = active_config.reward_floor
        boss_config.use_collect = active_config.use_collect
        boss_config.Attack_60 = active_config.Attack_60

        general_battle = self.config.area_boss.general_battle
        general_battle.lock_team_enable = active_config.lock_team_enable
        general_battle.preset_enable = active_config.switch_team_enable
        self.config.area_boss.switch_soul.enable = active_config.switch_soul_enable
        self.config.area_boss.switch_soul.switch_group_team = active_config.preset_public_enable
        self.config.area_boss.switch_soul.enable_switch_by_name = False
        self.config.area_boss.switch_soul.group_name = ""
        self.config.area_boss.switch_soul.team_name = ""

    def run_account(self, index: int, account: MultiAccountAreaBossAccount) -> bool | None:
        """直接调用原地域鬼王任务，复用其全部逻辑和优化。"""
        task_obj = self.create_task_object(
            "AreaBoss",
            config=self.config,
            device=self.device,
        )
        task_obj.set_next_run = self._skip_inner_task_schedule
        task_obj.run()
        return True

    def _skip_inner_task_schedule(self, *args, **kwargs) -> None:
        """防止原地域鬼王覆盖多账号地域鬼王的调度时间。"""

    def cleanup_account(
        self,
        index: int,
        account: MultiAccountAreaBossAccount,
        account_success: bool,
    ) -> None:
        """恢复原地域鬼王配置，保证下一个账号从原始配置开始。"""
        latest_outer_config = getattr(self.config.model, self.multi_account_config_attr, None)
        latest_scheduler = getattr(latest_outer_config, "scheduler", None)
        if latest_scheduler is not None:
            self.fade_conf.scheduler = copy.deepcopy(latest_scheduler)

        backup = getattr(self, "_area_boss_config_backup", None)
        if backup is None:
            return
        self.config.model.area_boss = backup
        del self._area_boss_config_backup
