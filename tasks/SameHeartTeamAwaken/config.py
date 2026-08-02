from pydantic import Field
from tasks.Component.SwitchSoul.switch_soul_config import SwitchSoulConfig
from tasks.Component.config_scheduler import Scheduler
from tasks.Component.config_base import ConfigBase, Time
from tasks.Component.GeneralBattle.config_general_battle import GeneralBattleConfig
from tasks.EvoZone.config import KirinType, Layer as EvoZoneLayer


class SameHeartTeamAwakenSwitchSoulConfig(SwitchSoulConfig):
    enable: bool = Field(default=False, description='是否启用御魂切换')
    switch_group_team: str = Field(default='-1,-1', description='御魂切换和队伍切换通用分组，例如 6,1')
    enable_switch_by_name: bool = Field(default=False, description='是否通过名称切换御魂')
    group_name: str = Field(default='', description='御魂组名')
    team_name: str = Field(default='', description='队伍名')


class SameHeartTeamAwakenConfig(ConfigBase):
    kirin_type: KirinType = Field(default=KirinType.LIGHTNINGKIRIN, description='kirin_type_help')
    layer: EvoZoneLayer = Field(default=EvoZoneLayer.TEN, description='layer_help')
    limit_time: Time = Field(default=Time(minute=30), description='limit_time_help')
    limit_count: int = Field(default=1, description='limit_count_help')
    soul_buff_enable: bool = Field(default=False, description='是否开启觉醒加成')
    lock_team_enable: bool = Field(default=False, description='是否锁定阵容')
    preset_enable: bool = Field(default=False, description='是否启动切换队伍预设')
    switch_soul_enable: bool = Field(default=False, description='是否启用切换御魂')
    switch_group_team: str = Field(default='-1,-1', description='御魂切换和队伍切换通用分组，例如 6,1')

    @property
    def general_battle_config(self) -> GeneralBattleConfig:
        preset_group = 1
        preset_team = 1
        try:
            group, team = [int(x) for x in self.switch_group_team.split(',')]
            if group > 0:
                preset_group = group
            if team > 0:
                preset_team = team
        except ValueError:
            pass
        return GeneralBattleConfig(
            lock_team_enable=self.lock_team_enable,
            preset_enable=self.preset_enable,
            preset_group=preset_group,
            preset_team=preset_team,
        )

    @property
    def switch_soul_config(self) -> SameHeartTeamAwakenSwitchSoulConfig:
        return SameHeartTeamAwakenSwitchSoulConfig(
            enable=self.switch_soul_enable,
            switch_group_team=self.switch_group_team,
            enable_switch_by_name=False,
            group_name='',
            team_name='',
        )


class SameHeartTeamAwaken(ConfigBase):
    scheduler: Scheduler = Field(default_factory=Scheduler)
    same_heart_team_awaken_config: SameHeartTeamAwakenConfig = Field(default_factory=SameHeartTeamAwakenConfig)
