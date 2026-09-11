# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
from datetime import timedelta
from enum import Enum
from pydantic import BaseModel, Field

from tasks.Component.config_scheduler import Scheduler as BaseScheduler
from tasks.Component.config_base import ConfigBase, TimeDelta

class Scheduler(BaseScheduler):
    success_interval: TimeDelta = Field(default=TimeDelta(hours=6), description='success_interval_help')
    failure_interval: TimeDelta = Field(default=TimeDelta(hours=6), description='failure_interval_help')

class LevelReward(str, Enum):
    ONE = '蛇皮/青吉鬼'
    TWO = '金币/勾玉'
    THREE = '体力/樱饼'

class TalismanConfig(BaseModel):
    get_flower: bool = Field(default=False, description='收取花合战等级奖励')
    level_reward: LevelReward = Field(default=LevelReward.TWO)
    # 领取成就奖励
    get_accomplishments: bool = Field(default=False, description='获取成就奖励')
    # 获取纳物库截图并发送通知
    get_nawu: bool = Field(default=False, title='纳物库', description='获取纳物库截图并发送通知')
    harvest_soul: bool = Field(default=False, description='收获1500签御魂')

class TalismanPass(ConfigBase):
    scheduler: Scheduler = Field(default_factory=Scheduler)
    talisman: TalismanConfig = Field(default_factory=TalismanConfig)

