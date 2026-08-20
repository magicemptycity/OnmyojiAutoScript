import copy
from datetime import datetime, timedelta
from typing import Any

from module.logger import logger
from tasks.Component.MultiAccount.multi_account_task import MultiAccountTaskBase
from tasks.Hunt.config import (
    Hunt,
    HuntConfig,
    HuntGeneralBattleConfig,
    HuntTime,
    NetherWorldBattleConfig,
)
from tasks.MultiAccountHunt.assets import MultiAccountHuntAssets
from tasks.MultiAccountHunt.config import (
    MultiAccountHunt,
    MultiAccountHuntAccount,
    MultiAccountHuntPrivateConfig,
)


class ScriptTask(MultiAccountTaskBase, MultiAccountHuntAssets):
    """多账号狩猎战的外层调度器。"""

    task_name = "MultiAccountHunt"
    multi_account_config_attr = "multi_account_hunt"
    inner_task_display_name = "狩猎战"
    server_schedule = False
    retry_delay = timedelta(minutes=10)
    fade_conf: MultiAccountHunt = None

    def get_account_config(
        self,
        index: int,
        account: MultiAccountHuntAccount,
    ) -> MultiAccountHuntPrivateConfig:
        if account.enable_private_config and index < len(self.fade_conf.private_config):
            return self.fade_conf.private_config[index]
        return self.fade_conf.common_config

    def collect_pending_accounts(self, now: datetime) -> list[tuple[int, Any]]:
        return [
            (index, account)
            for index, account in enumerate(self.fade_conf.account_list)
            if id(account) not in getattr(self, "_invalid_shared_account_ids", set())
            and account.is_valid()
            and self._is_account_in_scope(account)
            and (not account.next_hunt_time or account.next_hunt_time <= now)
        ]

    def get_next_run_time(self) -> datetime:
        next_times = [
            account.next_hunt_time
            for account in self.get_scoped_accounts()
            if id(account) not in getattr(self, "_invalid_shared_account_ids", set())
            and account.is_valid() and account.next_hunt_time
        ]
        if not next_times:
            return datetime.now() + timedelta(hours=1)
        return min(next_times)

    def prepare_account(self, index: int, account: MultiAccountHuntAccount) -> None:
        """临时覆盖内层狩猎战参数，不写入用户的普通狩猎战配置。"""
        self._hunt_config_backup = copy.deepcopy(self.config.model.hunt)
        mapped_config = self._build_hunt_config(self.get_account_config(index, account))
        hunt_config = self.config.model.hunt
        hunt_config.hunt_time = mapped_config.hunt_time
        hunt_config.hunt_config = mapped_config.hunt_config
        hunt_config.kirin_battle_config = mapped_config.kirin_battle_config
        hunt_config.netherworld_battle_config = mapped_config.netherworld_battle_config

    def run_account(self, index: int, account: MultiAccountHuntAccount) -> bool | None:
        task_obj = self.create_task_object("Hunt", config=self.config, device=self.device)
        task_obj.set_next_run = self._skip_inner_task_schedule
        task_obj.run()
        return True

    def on_account_success(self, index: int, account: MultiAccountHuntAccount) -> bool | None:
        active = self.get_account_config(index, account)
        self.fade_conf.update_account_next_hunt_time(
            account,
            self._next_hunt_time(datetime.now(), active),
        )
        account.last_complete_time = datetime.now()
        return True

    def on_account_failure(
        self,
        index: int,
        account: MultiAccountHuntAccount,
        reason: str,
    ) -> None:
        """失败账号延后重试，避免过期时间使任务立即循环运行。"""
        account.next_hunt_time = datetime.now() + self.retry_delay
        logger.warning(
            "%s-%s 本轮狩猎战失败（%s），安排在 %s 后重试",
            account.character,
            account.svr,
            reason,
            account.next_hunt_time,
        )

    def cleanup_account(
        self,
        index: int,
        account: MultiAccountHuntAccount,
        account_success: bool,
    ) -> None:
        backup = getattr(self, "_hunt_config_backup", None)
        if backup is None:
            return
        latest_outer_config = getattr(self.config.model, self.multi_account_config_attr, None)
        latest_scheduler = getattr(latest_outer_config, "scheduler", None)
        if latest_scheduler is not None:
            self.fade_conf.scheduler = copy.deepcopy(latest_scheduler)
        # 恢复内层配置字段。这里不替换 ConfigModel 顶层字段，避免触发自动保存。
        hunt_config = self.config.model.hunt
        hunt_config.hunt_time = backup.hunt_time
        hunt_config.hunt_config = backup.hunt_config
        hunt_config.kirin_battle_config = backup.kirin_battle_config
        hunt_config.netherworld_battle_config = backup.netherworld_battle_config
        del self._hunt_config_backup

    @staticmethod
    def _build_hunt_config(active: MultiAccountHuntPrivateConfig) -> Hunt:
        def battle_config(config_type, enabled: bool, preset: str, battle_timeout: int):
            data = {
                "preset_enable": enabled,
                "battle_timeout": battle_timeout,
            }
            if not enabled:
                return config_type.model_validate(data)

            try:
                group_text, team_text = preset.split(",")
                group, team = int(group_text.strip()), int(team_text.strip())
                if not 1 <= group <= 7 or not 1 <= team <= 5:
                    raise ValueError("预设组号必须为 1-7，队伍号必须为 1-5")
            except (AttributeError, TypeError, ValueError):
                logger.warning("无效的狩猎战预设分组：%s，已关闭队伍预设切换", preset)
                data["preset_enable"] = False
            else:
                data["preset_group"] = group
                data["preset_team"] = team
            return config_type.model_validate(data)

        return Hunt(
            hunt_time=HuntTime(
                kirin_time=active.kirin_time,
                netherworld_time=active.netherworld_time,
            ),
            hunt_config=HuntConfig(
                kirin_group_team=(
                    active.kirin_preset_public_enable
                    if active.kirin_switch_soul_enable
                    else "-1,-1"
                ),
                netherworld_group_team=(
                    active.netherworld_preset_public_enable
                    if active.netherworld_switch_soul_enable
                    else "-1,-1"
                ),
            ),
            # 多账号狩猎战只开放队伍预设和战斗超时；其余战斗参数保持原任务默认值。
            kirin_battle_config=battle_config(
                HuntGeneralBattleConfig,
                active.kirin_switch_team_enable,
                active.kirin_preset_public_enable,
                active.kirin_battle_timeout,
            ),
            netherworld_battle_config=battle_config(
                NetherWorldBattleConfig,
                active.netherworld_switch_team_enable,
                active.netherworld_preset_public_enable,
                active.netherworld_battle_timeout,
            ),
        )

    @staticmethod
    def _next_hunt_time(now: datetime, config: MultiAccountHuntPrivateConfig) -> datetime:
        for days in range(8):
            date = (now + timedelta(days=days)).date()
            is_kirin = date.weekday() <= 3
            event_time = config.kirin_time if is_kirin else config.netherworld_time
            candidate = datetime.combine(date, event_time)
            if candidate > now:
                return candidate
        raise RuntimeError("无法计算下一次狩猎战时间")

    @staticmethod
    def _skip_inner_task_schedule(*args, **kwargs) -> None:
        """防止内层狩猎战覆盖多账号任务的调度。"""
