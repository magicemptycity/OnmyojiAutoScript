
from pydantic import BaseModel, Field
from enum import Enum
from datetime import datetime, time

from tasks.Component.config_scheduler import Scheduler
from tasks.Component.config_base import ConfigBase, TimeDelta

class DelegationConfig(ConfigBase):
    # 弥助的画-300-六星变异卡   15小时
    miyoshino_painting: bool = Field(default=False, description='miyoshino_painting_help')
    # 鸟之羽-50-20片大蛇的逆鳞      24小时
    bird_feather: bool = Field(default=False, description='bird_feather_help')
    # 寻找耳环-300-金币28万     15小时
    find_earring: bool = Field(default=False, description='find_earring_help')
    # 猫老大-300-四星白蛋       12小时
    cat_boss: bool = Field(default=False, description='cat_boss_help')
    # 接送弥助-100-三星结界卡   8小时
    miyoshino: bool = Field(default=False, description='miyoshino_help')
    # 奇怪的痕迹-100-金币九万八     15小时
    strange_trace: bool = Field(default=False, description='strange_trace_help')

class Delegation(ConfigBase):
    scheduler: Scheduler = Field(default_factory=Scheduler)
    delegation_config: DelegationConfig = Field(default_factory=DelegationConfig)


