from enum import Enum

from pydantic import Field

from tasks.Component.SwitchSoul.switch_soul_config import SwitchSoulConfig
from tasks.Component.config_scheduler import Scheduler
from tasks.Component.config_base import ConfigBase, Time
from tasks.Component.GeneralBattle.config_general_battle import GeneralBattleConfig
from tasks.Orochi.config import Layer as OrochiLayer
from tasks.SameHeartTeam.config import resolve_preset


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
    soul_buff_enable: bool = Field(default=False, description='soul_buff_enable_help')
    # 是否锁定阵容
    lock_team_enable: bool = Field(default=False, description='lock_team_enable_help')
    # 预设队伍切换开关
    switch_team_enable: bool = Field(default=False, description='switch_team_enable_help')
    # 御魂切换开关
    switch_soul_enable: bool = Field(default=False, description='switch_soul_enable_help')
    # 预设队伍和御魂通用分组，例如 "6,1"，用于队伍预设和御魂切换共用分组配置
    preset_public_enable: str = Field(default='-1,-1', description='preset_public_enable_help')

    @property
    def general_battle_config(self) -> GeneralBattleConfig:
        # 解析 preset_public_enable 字符串为通用预设组和队伍编号
        preset_group, preset_team = resolve_preset(self.preset_public_enable)
        return GeneralBattleConfig(
            lock_team_enable=self.lock_team_enable,
            preset_enable=self.switch_team_enable,
            preset_group=preset_group,
            preset_team=preset_team,
        )

    @property
    def switch_soul_config(self) -> SameHeartTeamOrochiSwitchSoulConfig:
        # 将 preset_public_enable 传递给 SwitchSoulConfig 的 switch_group_team 字段
        return SameHeartTeamOrochiSwitchSoulConfig(
            enable=self.switch_soul_enable,
            switch_group_team=self.preset_public_enable,
            enable_switch_by_name=False,
            group_name='',
            team_name='',
        )


class SameHeartTeamOrochi(ConfigBase):
    scheduler: Scheduler = Field(default_factory=Scheduler)
    same_heart_team_orochi_config: SameHeartTeamOrochiConfig = Field(default_factory=SameHeartTeamOrochiConfig)
