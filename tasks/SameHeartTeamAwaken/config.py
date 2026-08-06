from pydantic import Field
from tasks.Component.SwitchSoul.switch_soul_config import SwitchSoulConfig
from tasks.Component.config_scheduler import Scheduler
from tasks.Component.config_base import ConfigBase, Time
from tasks.Component.GeneralBattle.config_general_battle import GeneralBattleConfig
from tasks.EvoZone.config import KirinType, Layer as EvoZoneLayer
from tasks.SameHeartTeam.config import resolve_preset


class SameHeartTeamAwakenSwitchSoulConfig(SwitchSoulConfig):
    enable: bool = Field(default=False, description='是否启用御魂切换')
    switch_group_team: str = Field(default='-1,-1', description='御魂切换和队伍切换通用分组，例如 6,1')
    enable_switch_by_name: bool = Field(default=False, description='是否通过名称切换御魂')
    group_name: str = Field(default='', description='御魂组名')
    team_name: str = Field(default='', description='队伍名')


class SameHeartTeamAwakenConfig(ConfigBase):
    # 麒麟选择
    kirin_type: KirinType = Field(default=KirinType.LIGHTNINGKIRIN, description='kirin_type_help')
    # 挑战层数
    layer: EvoZoneLayer = Field(default=EvoZoneLayer.TEN, description='layer_help')
    # 限制运行时间
    limit_time: Time = Field(default=Time(minute=30), description='limit_time_help')
    # 限制运行次数
    limit_count: int = Field(default=1, description='limit_count_help')
    # 是否开启加成
    soul_buff_enable: bool = Field(default=False, description='是否开启觉醒加成')
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
        # 解析 preset_public_enable 字符串，生成用于 GeneralBattle 的预设组/队参数
        preset_group, preset_team = resolve_preset(self.preset_public_enable)
        return GeneralBattleConfig(
            lock_team_enable=self.lock_team_enable,
            preset_enable=self.switch_team_enable,
            preset_group=preset_group,
            preset_team=preset_team,
        )

    @property
    def switch_soul_config(self) -> SameHeartTeamAwakenSwitchSoulConfig:
        # 这里把通用的 preset_public_enable 用于御魂切换分组参数
        return SameHeartTeamAwakenSwitchSoulConfig(
            enable=self.switch_soul_enable,
            switch_group_team=self.preset_public_enable,
            enable_switch_by_name=False,
            group_name='',
            team_name='',
        )


class SameHeartTeamAwaken(ConfigBase):
    scheduler: Scheduler = Field(default_factory=Scheduler)
    same_heart_team_awaken_config: SameHeartTeamAwakenConfig = Field(default_factory=SameHeartTeamAwakenConfig)
