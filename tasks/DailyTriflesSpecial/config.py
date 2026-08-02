# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
from datetime import datetime
from pydantic import BaseModel, Field

from tasks.Component.config_scheduler import Scheduler
from tasks.Component.config_base import ConfigBase, dynamic_hide
from enum import Enum


class SummonType(str, Enum):
    default = '普通召唤'
    recall = '今忆召唤'


class DailyTriflesSpecialConfig(BaseModel):
    # 庭院事务
    courtyard_affairs: bool = Field(default=True)
    # 收取邮件
    pickup_email: bool = Field(default=True)
    # 一键预存
    one_click_pre_deposit: bool = Field(default=False, description='同心队一键预存')
    one_summon: bool = Field(title='One Summon', default=False)
    # 召唤类型
    summon_type: SummonType = Field(default=SummonType.default, description='召唤类型')
    # 是否绘制神秘图案
    draw_mystery_pattern: bool = Field(title='Draw Mystery Pattern', default=False, description='是否绘制神秘图案')
    guild_wish: bool = Field(title='Guild Wish', default=False)
    luck_msg: bool = Field(title='Luck Msg', default=False)
    store_sign: bool = Field(title='Store Sign', default=False, description='store_sign_help')
    # 每天购买体力数量
    buy_sushi_count: int = Field(title='Buy Sushi Count', default=-1)

    hide_fields = dynamic_hide('draw_mystery_pattern')


class DailyTriflesSpecial(ConfigBase):
    scheduler: Scheduler = Field(default_factory=Scheduler)
    trifles_config: DailyTriflesSpecialConfig = Field(default_factory=DailyTriflesSpecialConfig)
