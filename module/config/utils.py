# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
import os
import json
import yaml

from filelock import FileLock
from datetime import datetime, timedelta, timezone, time

from module.config.atomicwrites import atomic_write
from module.logger import logger

DEFAULT_TIME = datetime(2023, 1, 1, 0, 0)

def filepath_config(filename, mod_name='script') -> str:
    """
    返回配置文件的路径
    """
    if mod_name == 'script':
        return os.path.join('./config', f'{filename}.json')
    else:
        return os.path.join('./config', f'{filename}.{mod_name}.json')

def filepath_args(filename='args', mod_name='alas'):
    return f'./module/config/argument/{filename}.json'


def filepath_argument(filename):
    return f'./module/config/argument/{filename}.yaml'


def read_file(file: str):
    """
    Read a file, support both .yaml and .json format.
    Return empty dict if file not exists.

    Args:
        file (str):

    Returns:
        dict, list:
    """
    folder = os.path.dirname(file)
    if not os.path.exists(folder):
        os.mkdir(folder)

    if not os.path.exists(file):
        return {}

    _, ext = os.path.splitext(file)
    lock = FileLock(f"{file}.lock")
    with lock:
        logger.debug(f'read: {file}')
        if ext == '.yaml':
            with open(file, mode='r', encoding='utf-8') as f:
                s = f.read()
                data = list(yaml.safe_load_all(s))
                if len(data) == 1:
                    data = data[0]
                if not data:
                    data = {}
                return data
        elif ext == '.json':
            with open(file, mode='r', encoding='utf-8') as f:
                s = f.read()
                return json.loads(s)
        else:
            logger.warning(f'Unsupported config file extension: {ext}')
            return {}

def write_file(file: str, data):
    """
    Write data into a file, supports both .yaml and .json format.

    Args:
        file (str):
        data (dict, list):
    """
    folder = os.path.dirname(file)
    if not os.path.exists(folder):
        os.mkdir(folder)

    _, ext = os.path.splitext(file)
    lock = FileLock(f"{file}.lock")
    with lock:
        logger.debug(f'write: {file}')
        if ext == '.yaml':
            with atomic_write(file, overwrite=True, encoding='utf-8', newline='') as f:
                if isinstance(data, list):
                    yaml.safe_dump_all(data, f, default_flow_style=False, encoding='utf-8', allow_unicode=True,
                                       sort_keys=False)
                else:
                    yaml.safe_dump(data, f, default_flow_style=False, encoding='utf-8', allow_unicode=True,
                                   sort_keys=False)
        elif ext == '.json':
            with atomic_write(file, overwrite=True, encoding='utf-8', newline='') as f:
                s = json.dumps(data, indent=2, ensure_ascii=False, sort_keys=False, default=str)
                f.write(s)
        else:
            logger.warning(f'Unsupported config file extension: {ext}')


def update_json_file(file: str, updater):
    """在同一跨进程文件锁内完成 JSON 的读取、合并和原子写入。"""
    file = os.fspath(file)
    folder = os.path.dirname(file)
    if folder and not os.path.exists(folder):
        os.makedirs(folder, exist_ok=True)
    lock = FileLock(f"{file}.lock")
    with lock:
        if os.path.exists(file):
            with open(file, mode="r", encoding="utf-8") as stream:
                source = json.loads(stream.read())
        else:
            source = {}
        updated = updater(source)
        with atomic_write(file, overwrite=True, encoding="utf-8", newline="") as stream:
            stream.write(
                json.dumps(
                    updated,
                    indent=2,
                    ensure_ascii=False,
                    sort_keys=False,
                    default=str,
                )
            )
        return updated


def deep_iter(data, depth=0, current_depth=1):
    """
    Iter a dictionary safely.

    Args:
        data (dict):
        depth (int): Maximum depth to iter
        current_depth (int):

    Returns:
        list: Key path
        Any:
    """
    if isinstance(data, dict) \
            and (depth and current_depth <= depth):
        for key, value in data.items():
            for child_path, child_value in deep_iter(value, depth=depth, current_depth=current_depth + 1):
                yield [key] + child_path, child_value
    else:
        yield [], data

def server_timezone() -> timedelta:
    return timedelta(hours=8)

def server_time_offset() -> timedelta:
    """
    To convert local time to server time:
        server_time = local_time + server_time_offset()
    To convert server time to local time:
        local_time = server_time - server_time_offset()
    """
    return datetime.now(timezone.utc).astimezone().utcoffset() - server_timezone()

def get_server_next_update(daily_trigger):
    """
    Args:
        daily_trigger (list[str], str): [ "00:00", "12:00", "18:00",]

    Returns:
        datetime.datetime
    """
    if isinstance(daily_trigger, str):
        daily_trigger = daily_trigger.replace(' ', '').split(',')

    diff = server_time_offset()
    local_now = datetime.now()
    trigger = []
    for t in daily_trigger:
        h, m = [int(x) for x in t.split(':')]
        future = local_now.replace(hour=h, minute=m, second=0, microsecond=0) + diff
        s = (future - local_now).total_seconds() % 86400
        future = local_now + timedelta(seconds=s)
        trigger.append(future)
    update = sorted(trigger)[0]
    return update


def convert_to_underscore(text: str) -> str:
    """
    大驼峰形式的字符串转换为下划线形式的字符串，并在数字前插入下划线。如果字符串中已经包含下划线，则会直接返回原始字符串。
    :param text:
    :return:
    """
    if '_' in text:
        # If text already contains underscore, assume it's in the correct format
        return text
    text = text.replace(' ', '')

    result = ''
    for i, char in enumerate(text):
        if char.isupper():
            if i > 0 and (text[i-1].islower() or (i < len(text) - 1 and text[i+1].islower())):
                # Insert underscore before uppercase letter, except at the beginning
                result += '_'
            result += char.lower()
        elif char.isdigit():
            if i > 0 and (text[i-1].isalpha() or (i < len(text) - 1 and text[i+1].isalpha())):
                # Insert underscore before digit, except at the beginning
                result += '_'
            result += char
        else:
            result += char

    return result

def get_server_last_update(daily_trigger):
    """
    Args:
        daily_trigger (list[str], str): [ "00:00", "12:00", "18:00",]

    Returns:
        datetime.datetime
    """
    if isinstance(daily_trigger, str):
        daily_trigger = daily_trigger.replace(' ', '').split(',')

    diff = server_time_offset()
    local_now = datetime.now()
    trigger = []
    for t in daily_trigger:
        h, m = [int(x) for x in t.split(':')]
        future = local_now.replace(hour=h, minute=m, second=0, microsecond=0) + diff
        s = (future - local_now).total_seconds() % 86400 - 86400
        future = local_now + timedelta(seconds=s)
        trigger.append(future)
    update = sorted(trigger)[-1]
    return update


def nearest_future(future, interval=120):
    """
    Get the neatest future time.
    Return the last one if two things will finish within `interval`.

    Args:
        future (list[datetime.datetime]):
        interval (int): Seconds

    Returns:
        datetime.datetime:
    """
    future = [datetime.fromisoformat(f) if isinstance(f, str) else f for f in future]
    future = sorted(future)
    next_run = future[0]
    for finish in future:
        if finish - next_run < timedelta(seconds=interval):
            next_run = finish

    return next_run



def dict_to_kv(dictionary, allow_none=True):
    """
    Args:
        dictionary: Such as `{'path': 'Scheduler.ServerUpdate', 'value': True}`
        allow_none (bool):

    Returns:
        str: Such as `path='Scheduler.ServerUpdate', value=True`
    """
    return ', '.join([f'{k}={repr(v)}' for k, v in dictionary.items() if allow_none or v is not None])


def parse_tomorrow_server(server_update: time, delay_date: int = 1, float_seconds: int = 0) -> datetime:
    """
    获取明天的日期，给这个日期加上server_update的时间，返回datetime
    :param server_update:
    :param float_seconds: 浮动的秒数，可为正或负值
    :return:
    """
    if isinstance(server_update, str):
        server_update = time.fromisoformat(server_update)
    now = datetime.now()
    tomorrow = now + timedelta(days=delay_date)
    next_run = datetime.combine(tomorrow, server_update)
    
    # 应用浮动时间
    if float_seconds !=0:
        next_run += timedelta(seconds=float_seconds)

        # 确保时间在第二天内，不回退到前一天，不跨越到第三天（考虑任务时间，最早 00:00，最晚 23:50）
        start_of_tomorrow = datetime.combine(tomorrow, time.min)
        end_of_tomorrow = datetime.combine(tomorrow, time(hour=23, minute=50))
    
        if next_run < start_of_tomorrow:
            next_run = start_of_tomorrow
        elif next_run > end_of_tomorrow:
            next_run = end_of_tomorrow
    
    return next_run


def parse_next_server_weekday(server_update: time, weekdays: list[int], float_seconds: int = 0) -> datetime:
    """返回下一个指定星期的强制运行时间。星期使用 ISO 编号，周一为 1、周日为 7。"""
    if isinstance(server_update, str):
        server_update = time.fromisoformat(server_update)

    valid_weekdays = sorted({day for day in weekdays if 1 <= day <= 7})
    if not valid_weekdays:
        # 空选择不是有效的星期规则，按每天处理，避免调度器失去下次运行时间。
        valid_weekdays = list(range(1, 8))

    now = datetime.now()
    for offset in range(8):
        candidate_date = now.date() + timedelta(days=offset)
        candidate = datetime.combine(candidate_date, server_update)
        if candidate > now and candidate_date.isoweekday() in valid_weekdays:
            next_run = candidate
            break
    else:
        raise ValueError(f'Unable to calculate next weekday run time: {valid_weekdays}')

    if float_seconds:
        next_run += timedelta(seconds=float_seconds)
        start_of_day = datetime.combine(next_run.date(), time.min)
        end_of_day = datetime.combine(next_run.date(), time(hour=23, minute=50))
        next_run = max(start_of_day, min(next_run, end_of_day))

    return next_run


def parse_next_server_random_weekday(
    scheduler,
    float_seconds: int = 0,
    now: datetime | None = None,
) -> datetime:
    """按周保存随机星期，并返回下一个随机运行时间。

    仅从 ``scheduler.weekdays`` 中抽取；周中首次启用时优先从本周尚未
    过去的日期抽取。本周没有可用日期时直接为下一周抽签。
    """
    import random

    now = (now or datetime.now()).replace(microsecond=0)
    server_update = scheduler.server_update
    if isinstance(server_update, str):
        server_update = time.fromisoformat(server_update)
    allowed = sorted({int(day) for day in scheduler.weekdays if 1 <= int(day) <= 7})
    if not allowed:
        allowed = list(range(1, 8))
    count = min(max(int(getattr(scheduler, 'random_week_days', 1)), 1), len(allowed))

    for week_offset in range(2):
        reference = now.date() + timedelta(days=7 * week_offset)
        iso_year, iso_week, _ = reference.isocalendar()
        week_key = f'{iso_year}-W{iso_week:02d}'
        week_start = reference - timedelta(days=reference.isoweekday() - 1)
        candidates = list(allowed)
        if week_offset == 0 and getattr(scheduler, 'random_week_key', '') != week_key:
            remaining = [
                day for day in allowed
                if datetime.combine(week_start + timedelta(days=day - 1), server_update) > now
            ]
            if remaining:
                candidates = remaining
            else:
                continue
        selected = sorted({
            int(day) for day in getattr(scheduler, 'random_weekdays', [])
            if int(day) in allowed
        })
        rule_key = f'{allowed}:{count}'
        same_week = getattr(scheduler, 'random_week_key', '') == week_key
        same_rule = getattr(scheduler, 'random_week_rule', '') == rule_key
        manual = bool(getattr(scheduler, 'random_week_manual', False))
        if not same_week or not same_rule or not selected:
            selected = sorted(random.sample(candidates, min(count, len(candidates))))
            scheduler.random_week_key = week_key
            scheduler.random_weekdays = selected
            scheduler.random_week_rule = rule_key
            scheduler.random_week_manual = False
            logger.info(f'Weekly random schedule {week_key}: {selected}')
        elif manual:
            logger.debug(f'Use manually adjusted weekly schedule {week_key}: {selected}')

        for weekday in selected:
            candidate = datetime.combine(week_start + timedelta(days=weekday - 1), server_update)
            if candidate > now:
                if float_seconds:
                    candidate += timedelta(seconds=float_seconds)
                    start = datetime.combine(candidate.date(), time.min)
                    end = datetime.combine(candidate.date(), time(hour=23, minute=50))
                    candidate = max(start, min(candidate, end))
                return candidate

    raise ValueError('Unable to calculate next weekly random run time')


def apply_random_week_schedule_edit(scheduler, argument: str, value):
    """应用用户对随机周调度字段的修改，并维护手动/自动状态。"""
    argument = str(argument)
    setattr(scheduler, argument, value)
    if argument == 'random_weekdays':
        selected = sorted({int(day) for day in value})
        allowed = sorted({int(day) for day in scheduler.weekdays})
        expected = min(int(scheduler.random_week_days), len(allowed))
        if not selected:
            raise ValueError('本周运行星期至少选择一天')
        if any(day not in allowed for day in selected):
            raise ValueError('本周运行星期必须位于候选运行星期范围内')
        if len(selected) != expected:
            raise ValueError(f'本周运行星期必须选择 {expected} 天')
        now = datetime.now()
        iso_year, iso_week, _ = now.isocalendar()
        scheduler.random_week_key = f'{iso_year}-W{iso_week:02d}'
        scheduler.random_weekdays = selected
        scheduler.random_week_rule = f'{allowed}:{expected}'
        scheduler.random_week_manual = True
    elif argument in {'schedule_mode', 'weekdays', 'random_week_days'}:
        scheduler.random_week_key = ''
        scheduler.random_weekdays = []
        scheduler.random_week_rule = ''
        scheduler.random_week_manual = False
    return scheduler


def parse_next_server_schedule(scheduler, float_seconds: int = 0) -> datetime:
    """计算非默认 09:00 强制日期规则的下次运行时间。"""
    mode = getattr(getattr(scheduler, 'schedule_mode', 'interval_days'), 'value',
                   getattr(scheduler, 'schedule_mode', 'interval_days'))
    if mode == 'weekday':
        return parse_next_server_weekday(
            scheduler.server_update, scheduler.weekdays, float_seconds
        )
    if mode == 'random_week':
        return parse_next_server_random_weekday(scheduler, float_seconds)
    return parse_tomorrow_server(
        scheduler.server_update, scheduler.delay_date, float_seconds
    )


def deep_get(d, keys, default=None):
    """
    Get values in dictionary safely.
    https://stackoverflow.com/questions/25833613/safe-method-to-get-value-of-nested-dictionary

    Args:
        d (dict):
        keys (str, list): Such as `Scheduler.NextRun.value`
        default: Default return if key not found.

    Returns:

    """
    if isinstance(keys, str):
        keys = keys.split('.')
    assert type(keys) is list
    if d is None:
        return default
    if not keys:
        return d
    return deep_get(d.get(keys[0]), keys[1:], default)


def deep_set(d, keys, value):
    """
    Set value into dictionary safely, imitating deep_get().
    """
    if isinstance(keys, str):
        keys = keys.split('.')
    assert type(keys) is list
    if not keys:
        return value
    if not isinstance(d, dict):
        d = {}
    d[keys[0]] = deep_set(d.get(keys[0], {}), keys[1:], value)
    return d


def deep_pop(d, keys, default=None):
    """
    Pop value from dictionary safely, imitating deep_get().
    """
    if isinstance(keys, str):
        keys = keys.split('.')
    assert type(keys) is list
    if not isinstance(d, dict):
        return default
    if not keys:
        return default
    elif len(keys) == 1:
        return d.pop(keys[0], default)
    return deep_pop(d.get(keys[0]), keys[1:], default)






if __name__ == '__main__':
    print(parse_tomorrow_server("09:01:00"))
