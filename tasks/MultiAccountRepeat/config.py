from datetime import datetime, timedelta
from typing import Any, Dict

from pydantic import Field, ValidationError, model_serializer, model_validator

from deploy.logger import logger
from tasks.MultiAccountRepeat.task_name_resolver import TaskNameResolver
from tasks.Component.SwitchAccount.switch_account_config import AccountInfo
from tasks.Component.config_base import ConfigBase, MultiLine
from tasks.Component.config_scheduler import Scheduler


class MultiAccountRepeatConfig(ConfigBase, extra='allow'):
    account_count: int = Field(default=1, ge=1, description='账号数量，决定下面会生成几组账号配置')
    skip_if_logged_today: bool = Field(default=True, description='如果上次登录时间为今天，则跳过该账号的登录与任务执行')

class MultiAccountRepeatAccount(AccountInfo):
    repeat_task_list: MultiLine = Field(default='', description='需要重复执行的任务名称，多个任务请换行填写。例如：悬赏封印\n契灵之境')

    @property
    def repeat_task_names(self) -> list[str]:
        names = []
        for line in self.repeat_task_list.split('\n'):
            raw = line.strip()
            if not raw:
                continue
            resolved = TaskNameResolver.resolve(raw)
            names.append(resolved or raw)
        return names


class MultiAccountRepeat(ConfigBase):
    scheduler: Scheduler = Field(default_factory=Scheduler)
    multi_account_repeat_config: MultiAccountRepeatConfig = Field(default_factory=MultiAccountRepeatConfig)
    account_list: list[MultiAccountRepeatAccount] = Field(default_factory=list)

    def update_account_login_history(self, account: MultiAccountRepeatAccount):
        for info in self.account_list:
            if info.character != account.character or info.svr != account.svr:
                continue
            info.last_complete_time = datetime.now()
            break

    @model_validator(mode='before')
    @classmethod
    def validator_all(cls, v: dict) -> Any:
        if not isinstance(v, dict):
            return v

        account_count = v.get('multi_account_repeat_config', {}).get('account_count', 1)
        data = dict(v)
        data.setdefault('account_list', [])

        remove_keys = []
        for key, value in data.items():
            if key == 'account_list' or 'account_list' not in key:
                continue
            try:
                item = MultiAccountRepeatAccount(**value)
                if item.is_valid():
                    data['account_list'].append(item)
                remove_keys.append(key)
            except (ValidationError, TypeError):
                pass

        for item in data['account_list']:
            if not hasattr(item, 'repeat_task_list'):
                item.repeat_task_list = ''
            elif isinstance(item.repeat_task_list, str):
                continue
            elif isinstance(item.repeat_task_list, dict):
                item.repeat_task_list = item.repeat_task_list.get('repeat_task_list', '')

        for key in remove_keys:
            data.pop(key, None)

        while len(data['account_list']) < account_count:
            data['account_list'].append(MultiAccountRepeatAccount())

        return data

    @model_serializer()
    def serializer_model(self, value: Any) -> Dict[str, Any]:
        properties = self.__dict__
        data = {}

        def v_dump(v):
            try:
                return v.model_dump()
            except AttributeError as e:
                logger.error(e)
                return v

        for key, value in properties.items():
            if isinstance(value, list):
                for index, v in enumerate(value):
                    data[f'{key}_{index + 1}'] = v_dump(v)
            else:
                data[key] = v_dump(value)
        return data
