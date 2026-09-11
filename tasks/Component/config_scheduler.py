# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
from enum import Enum

from pydantic import Field

from tasks.Component.config_base import ConfigBase, TimeDelta, DateTime, Time, Weekdays


class ScheduleMode(str, Enum):
    INTERVAL = 'interval_days'
    WEEKDAY = 'weekday'


class Scheduler(ConfigBase):
    enable: bool = Field(default=False, description='enable_help')
    next_run: DateTime = Field(default=DateTime.fromisoformat("2023-01-01 00:00:00"), description='next_run_help')
    priority: int = Field(default=5, description='priority_help')

    success_interval: TimeDelta = Field(default=TimeDelta(days=1), description='success_interval_help')
    failure_interval: TimeDelta = Field(default=TimeDelta(days=1), description='failure_interval_help')
    server_update: Time = Field(default=Time(hour=9, minute=0, second=0), description='server_update_help')
    schedule_mode: ScheduleMode = Field(
        default=ScheduleMode.INTERVAL,
        description='强制设定服务执行时间不为 09:00:00 时生效，选择按间隔天数或指定星期计算下次运行时间。',
    )
    delay_date: int = Field(default=1, description='delay_date_help', ge=1, le=31)
    # ISO 星期序号：1 为周一，7 为周日；默认每天。
    weekdays: Weekdays = Field(
        default=[1, 2, 3, 4, 5, 6, 7],
        description='选择任务允许运行的星期，只有强制日期规则为指定星期时生效。',
    )
    float_time: Time = Field(default=Time(hour=0, minute=0, second=0), description='float_time_help')


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




