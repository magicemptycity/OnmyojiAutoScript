from enum import Enum

from pydantic import Field

from tasks.Component.SwitchSoul.switch_soul_config import SwitchSoulConfig
from tasks.Component.config_scheduler import Scheduler
from tasks.Component.config_base import ConfigBase, Time
from tasks.Component.GeneralBattle.config_general_battle import GeneralBattleConfig
from tasks.Orochi.config import Layer as OrochiLayer


class SameHeartTeamOrochiSwitchSoulConfig(SwitchSoulConfig):
    enable: bool = Field(default=False, description='same_heart_team_orochi_switch_soul_enable_help')
    switch_group_team: str = Field(default='-1,-1', description='same_heart_team_orochi_switch_group_team_help')
    enable_switch_by_name: bool = Field(default=False, description='same_heart_team_orochi_enable_switch_by_name_help')
    group_name: str = Field(default='', description='same_heart_team_orochi_switch_group_name_help')
    team_name: str = Field(default='', description='same_heart_team_orochi_switch_team_name_help')


class SameHeartTeamOrochiConfig(ConfigBase):
    # 挑战层数
    layer: OrochiLayer = Field(default=OrochiLayer.ONE, description='layer_help')
    # 限制运行时间
    limit_time: Time = Field(default=Time(minute=30), description='limit_time_help')
    # 限制运行次数
    limit_count: int = Field(default=1, description='limit_count_help')
    # 是否开启加成
    soul_buff_enable: bool = Field(default=False, description='是否开启御魂加成')
    # 是否锁定阵容
    lock_team_enable: bool = Field(default=False, description='是否锁定阵容')
    # 是否启动切换预设
    preset_enable: bool = Field(default=False, description='是否启动切换队伍预设')
    # 是否启用切换御魂
    switch_soul_enable: bool = Field(default=False, description='是否启用切换御魂')
    # 御魂&预设分组，格式示例 6,1
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
    def switch_soul_config(self) -> SameHeartTeamOrochiSwitchSoulConfig:
        return SameHeartTeamOrochiSwitchSoulConfig(
            enable=self.switch_soul_enable,
            switch_group_team=self.switch_group_team,
            enable_switch_by_name=False,
            group_name='',
            team_name='',
        )


class SameHeartTeamOrochi(ConfigBase):
    scheduler: Scheduler = Field(default_factory=Scheduler)
    same_heart_team_orochi_config: SameHeartTeamOrochiConfig = Field(default_factory=SameHeartTeamOrochiConfig)
