from pydantic import BaseModel, Field
from enum import Enum
from tasks.Component.GeneralBattle.config_general_battle import GeneralBattleConfig
from tasks.Component.SwitchSoul.switch_soul_config import SwitchSoulConfig
from tasks.Component.config_base import ConfigBase, Time
from tasks.Component.config_scheduler import Scheduler



class RaidConfig(BaseModel):
    # 限制时间
    limit_time: Time = Field(default=Time(minute=30), description='limit_time_help')
    # 限制次数
    limit_count: int = Field(default=50, description='limit_count_help')
    # 攻破完成后指定下次运行时间
    next_ryoutoppa_time: Time = Field(default=Time(hour=7, minute=0, second=0))
    # 是否跳过难度较高的结界，失败后不再攻打该结界
    skip_difficult: bool = Field(default=True, description='skip_difficult_help')
    # 寮管理开启寮突破
    ryou_access: bool = Field(default=False, description='ryou_access_help')
    # 选择下一个目标前随机等待范围；填写 0 或 0,0 表示关闭。
    # 保留 bool 类型兼容旧配置，旧的 false/true 由执行器兼容处理。
    random_delay: str | bool = Field(default="0", description='random_delay_help')
    # 点击进攻按钮前随机等待范围；填写 0 表示关闭。
    fire_delay: str = Field(default="2,5", description='fire_delay_help')

    # 打完没票了 0/6 => 失败
    # 突破压根没开  +> 失败
    # 时间打满了  成功
    # 次数打满了  成功
    # 打完了（有失败的但是大不了） 成功



class RyouToppa(ConfigBase):
    scheduler: Scheduler = Field(default_factory=Scheduler)
    raid_config: RaidConfig = Field(default_factory=RaidConfig)
    general_battle_config: GeneralBattleConfig = Field(default_factory=GeneralBattleConfig)
    switch_soul_config: SwitchSoulConfig = Field(default_factory=SwitchSoulConfig)
