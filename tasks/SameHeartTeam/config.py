# This Python file uses the following encoding: utf-8
# @author runhey
from datetime import datetime
from pydantic import Field
from enum import Enum

from tasks.Component.config_scheduler import Scheduler
from tasks.Component.config_base import ConfigBase, DateTime, Time
from tasks.Component.SwitchSoul.switch_soul_config import SwitchSoulConfig
from tasks.Component.GeneralBattle.config_general_battle import GeneralBattleConfig
from tasks.Orochi.config import Layer as OrochiLayer
from tasks.EvoZone.config import Layer as EvoZoneLayer, KirinType as EvoZoneKirinType


def resolve_preset(raw_value: str | None, default_group: int = 1, default_team: int = 1) -> tuple[int, int]:
    """解析 组,队 模式字符串，如 '6,1'，非法值或非正值回退到默认值。"""
    if raw_value is None:
        return default_group, default_team

    text = str(raw_value).strip()
    if not text:
        return default_group, default_team

    try:
        group_text, team_text = [item.strip() for item in text.split(',', 1)]
        group = int(group_text)
        team = int(team_text)
    except (AttributeError, TypeError, ValueError):
        return default_group, default_team

    if group <= 0:
        group = default_group
    if team <= 0:
        team = default_team
    return group, team


def _resolve_preset_group_team(raw_value: str, default_group: int = 1, default_team: int = 1) -> tuple[int, int]:
    return resolve_preset(raw_value, default_group, default_team)


class SameHeartTeamMode(str, Enum):
    OROCHI = '御魂'
    AWAKEN = '觉醒'


class SameHeartTeamTeamNum(str, Enum):
    ONE = '1人'
    TWO = '2人'


class SameHeartTeamSwitchSoulConfig(SwitchSoulConfig):
    enable: bool = Field(default=False, description='same_heart_team_switch_soul_enable_help')
    switch_group_team: str = Field(default='-1,-1', description='same_heart_team_switch_group_team_help')
    enable_switch_by_name: bool = Field(default=False, description='same_heart_team_enable_switch_by_name_help')
    group_name: str = Field(default='', description='same_heart_team_switch_group_name_help')
    team_name: str = Field(default='', description='same_heart_team_switch_team_name_help')


class SameHeartTeamCommonConfig(ConfigBase):
    # 选择要执行的副本类型
    same_heart_team_mode: SameHeartTeamMode = Field(default=SameHeartTeamMode.OROCHI, description='same_heart_team_mode_help')
    # 选择人数：1人或2人
    team_num: SameHeartTeamTeamNum = Field(default=SameHeartTeamTeamNum.TWO, description='same_heart_team_team_num_help')


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
    # 只保留三个核心项：御魂切换开关、预设队伍切换开关、分组输入
    switch_soul_enable: bool = Field(default=False, description='switch_soul_config')
    preset_enable: bool = Field(default=False, description='preset_enable_help')
    switch_group_team: str = Field(default='-1,-1', description='御魂切换和队伍切换通用')

    @property
    def general_battle_config(self) -> GeneralBattleConfig:
        preset_group, preset_team = _resolve_preset_group_team(self.switch_group_team)
        return GeneralBattleConfig(
            lock_team_enable=self.lock_team_enable,
            preset_enable=self.preset_enable,
            preset_group=preset_group,
            preset_team=preset_team,
        )

    @property
    def switch_soul_config(self) -> SameHeartTeamSwitchSoulConfig:
        return SameHeartTeamSwitchSoulConfig(
            enable=self.switch_soul_enable,
            switch_group_team=self.switch_group_team,
            enable_switch_by_name=False,
            group_name='',
            team_name='',
        )


class SameHeartTeamAwakenConfig(ConfigBase):
    # 麒麟选择
    kirin_type: EvoZoneKirinType = Field(default=EvoZoneKirinType.LIGHTNINGKIRIN, description='kirin_type_help')
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
    # 只保留三个核心项：御魂切换开关、预设队伍切换开关、分组输入
    switch_soul_enable: bool = Field(default=False, description='是否启用御魂切换')
    preset_enable: bool = Field(default=False, description='是否启用队伍切换')
    switch_group_team: str = Field(default='-1,-1', description='御魂切换和队伍切换通用')

    @property
    def general_battle_config(self) -> GeneralBattleConfig:
        preset_group, preset_team = _resolve_preset_group_team(self.switch_group_team)
        return GeneralBattleConfig(
            lock_team_enable=self.lock_team_enable,
            preset_enable=self.preset_enable,
            preset_group=preset_group,
            preset_team=preset_team,
        )

    @property
    def switch_soul_config(self) -> SameHeartTeamSwitchSoulConfig:
        return SameHeartTeamSwitchSoulConfig(
            enable=self.switch_soul_enable,
            switch_group_team=self.switch_group_team,
            enable_switch_by_name=False,
            group_name='',
            team_name='',
        )


class SameHeartTeam(ConfigBase):
    scheduler: Scheduler = Field(default_factory=Scheduler)
    common_config: SameHeartTeamCommonConfig = Field(default_factory=SameHeartTeamCommonConfig)
    orochi_config: SameHeartTeamOrochiConfig = Field(default_factory=SameHeartTeamOrochiConfig)
    awaken_config: SameHeartTeamAwakenConfig = Field(default_factory=SameHeartTeamAwakenConfig)
