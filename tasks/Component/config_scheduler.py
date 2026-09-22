# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
from enum import Enum

from pydantic import Field

from tasks.Component.config_base import ConfigBase, TimeDelta, DateTime, Time, Weekdays, dynamic_hide


class ScheduleMode(str, Enum):
    INTERVAL = 'interval_days'
    WEEKDAY = 'weekday'
    RANDOM_WEEK = 'random_week'


class ClickReactionDelayMode(str, Enum):
    GLOBAL = 'Global'
    DISABLED = 'Disabled'
    CUSTOM = 'Custom'


class Scheduler(ConfigBase):
    enable: bool = Field(default=False, description='enable_help')
    next_run: DateTime = Field(default=DateTime.fromisoformat("2023-01-01 00:00:00"), description='next_run_help')
    priority: int = Field(default=5, description='priority_help')

    success_interval: TimeDelta = Field(default=TimeDelta(days=1), description='success_interval_help')
    failure_interval: TimeDelta = Field(default=TimeDelta(days=1), description='failure_interval_help')
    server_update: Time = Field(default=Time(hour=9, minute=0, second=0), description='server_update_help')
    schedule_mode: ScheduleMode = Field(
        default=ScheduleMode.INTERVAL,
        description='强制设定服务执行时间不为 09:00:00 时生效，可选择按间隔天数、指定星期或每周随机计算下次运行时间。',
    )
    delay_date: int = Field(default=1, description='delay_date_help', ge=1, le=31)
    # ISO 星期序号：1 为周一，7 为周日；默认每天。
    weekdays: Weekdays = Field(
        default=[1, 2, 3, 4, 5, 6, 7],
        description='选择任务允许运行的星期；指定星期直接使用，每周随机则从中抽取。',
    )
    random_week_days: int = Field(
        default=1,
        ge=1,
        le=7,
        description='每周随机模式下，从允许运行星期中随机选择的天数。',
    )
    # 以下两个字段用于保存抽签结果，保证同一周内重启后不会重新抽取。
    random_week_key: str = Field(default='', description='本周随机结果所属 ISO 周。')
    random_weekdays: Weekdays = Field(
        default_factory=list,
        title='本周运行星期',
        description='本周实际生效的运行星期，可手动调整；下周会恢复自动随机。',
    )
    random_week_rule: str = Field(default='', description='生成本周随机结果时使用的规则。')
    random_week_manual: bool = Field(default=False, description='本周运行星期是否由用户手动调整。')
    hide_random_week_state = dynamic_hide(
        'random_week_key', 'random_week_rule', 'random_week_manual'
    )
    float_time: Time = Field(default=Time(hour=0, minute=0, second=0), description='float_time_help')
    click_reaction_delay_mode: ClickReactionDelayMode = Field(
        default=ClickReactionDelayMode.GLOBAL,
        description='click_reaction_delay_mode_help',
    )
    click_reaction_delay_range: str = Field(
        default='0.18,0.22',
        description='task_click_reaction_delay_range_help',
    )


class CustomClickReactionScheduler(Scheduler):
    """用于原本已有独立点击延迟的任务，兼容旧配置缺少三态字段。"""
    click_reaction_delay_mode: ClickReactionDelayMode = Field(
        default=ClickReactionDelayMode.CUSTOM,
        description='click_reaction_delay_mode_help',
    )


if __name__ == "__main__":
    dict_s = {
        "enable": False,
        "next_run": "2026-07-19T14:15:37",
        "priority": 5,
        "success_interval": "10 00:00:01",
        "failure_interval": "10 00:00:01",
        "server_update": "09:03:00",
        "float_time": "02:00:05"
    }
    s = Scheduler(**dict_s)
    print(s.model_dump())




