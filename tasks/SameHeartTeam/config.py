# This Python file uses the following encoding: utf-8
# @author runhey
from datetime import datetime
from pydantic import Field
from enum import Enum

from tasks.Component.config_scheduler import Scheduler
from tasks.Component.config_base import ConfigBase, DateTime, Time
from tasks.Component.SwitchSoul.switch_soul_config import SwitchSoulConfig
from tasks.Component.GeneralBattle.config_general_battle import GeneralBattleConfig
from tasks.Component.GeneralInvite.config_invite import InviteConfig
from tasks.Orochi.config import Layer as OrochiLayer
from tasks.EvoZone.config import Layer as EvoZoneLayer, KirinType as EvoZoneKirinType


class SameHeartTeamMode(str, Enum):
    OROCHI = '御魂'
    AWAKEN = '觉醒'


class SameHeartTeamTeamNum(str, Enum):
    ONE = '1'
    TWO = '2'


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
    team_num: SameHeartTeamTeamNum = Field(default=SameHeartTeamTeamNum.ONE, description='same_heart_team_team_num_help')


class SameHeartTeamOrochiConfig(ConfigBase):
    # 挑战层数
    layer: OrochiLayer = Field(default=OrochiLayer.ONE, description='same_heart_team_orochi_layer_help')
    # 限制运行时间
    limit_time: Time = Field(default=Time(minute=30), description='same_heart_team_orochi_limit_time_help')
    # 限制运行次数
    limit_count: int = Field(default=1, description='same_heart_team_orochi_limit_count_help')
    # 是否开启加成
    soul_buff_enable: bool = Field(default=False, description='same_heart_team_orochi_buff_help')
    # 是否锁定阵容
    lock_team_enable: bool = Field(default=False, description='same_heart_team_orochi_lock_help')
    # 只保留三个核心项：御魂切换开关、预设队伍切换开关、分组输入
    switch_soul_enable: bool = Field(default=False, description='是否启用御魂切换')
    preset_enable: bool = Field(default=False, description='是否启用队伍切换')
    switch_group_team: str = Field(default='-1,-1', description='御魂切换和队伍切换通用')

    @property
    def general_battle_config(self) -> GeneralBattleConfig:
        return GeneralBattleConfig(
            lock_team_enable=self.lock_team_enable,
            preset_enable=self.preset_enable,
            preset_group=1,
            preset_team=1,
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
    kirin_type: EvoZoneKirinType = Field(default=EvoZoneKirinType.LIGHTNINGKIRIN, description='same_heart_team_awaken_kirin_help')
    # 挑战层数
    layer: EvoZoneLayer = Field(default=EvoZoneLayer.TEN, description='same_heart_team_awaken_layer_help')
    # 限制运行时间
    limit_time: Time = Field(default=Time(minute=30), description='same_heart_team_awaken_limit_time_help')
    # 限制运行次数
    limit_count: int = Field(default=1, description='same_heart_team_awaken_limit_count_help')
    # 是否开启加成
    soul_buff_enable: bool = Field(default=False, description='same_heart_team_awaken_buff_help')
    # 是否锁定阵容
    lock_team_enable: bool = Field(default=False, description='same_heart_team_awaken_lock_help')
    # 只保留三个核心项：御魂切换开关、预设队伍切换开关、分组输入
    switch_soul_enable: bool = Field(default=False, description='是否启用御魂切换')
    preset_enable: bool = Field(default=False, description='是否启用队伍切换')
    switch_group_team: str = Field(default='-1,-1', description='御魂切换和队伍切换通用')

    @property
    def general_battle_config(self) -> GeneralBattleConfig:
        return GeneralBattleConfig(
            lock_team_enable=self.lock_team_enable,
            preset_enable=self.preset_enable,
            preset_group=1,
            preset_team=1,
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


class SameHeartTeamDoneRecord(ConfigBase):
    same_heart_team_dt: DateTime = Field(default=DateTime.fromisoformat("2023-01-01 00:00:00"))


class SameHeartTeam(ConfigBase):
    scheduler: Scheduler = Field(default_factory=Scheduler)
    common_config: SameHeartTeamCommonConfig = Field(default_factory=SameHeartTeamCommonConfig)
    orochi_config: SameHeartTeamOrochiConfig = Field(default_factory=SameHeartTeamOrochiConfig)
    awaken_config: SameHeartTeamAwakenConfig = Field(default_factory=SameHeartTeamAwakenConfig)
    invite_config: InviteConfig = Field(default_factory=InviteConfig)
    done_record: SameHeartTeamDoneRecord = Field(default_factory=SameHeartTeamDoneRecord)

    def today_is_done(self, mode: str) -> bool:
        done_dt = getattr(self.done_record, f'{mode}_dt', None)
        if done_dt is None:
            return False
        return done_dt.date() == datetime.today().date()
