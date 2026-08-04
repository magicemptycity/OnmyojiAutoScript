from datetime import datetime
from enum import Enum
from typing import Any, Dict

from pydantic import Field, ValidationError, model_serializer, model_validator

from deploy.logger import logger
from tasks.Component.SwitchAccount.switch_account_config import AccountInfo
from tasks.Component.config_base import ConfigBase, DateTime
from tasks.Component.config_scheduler import Scheduler
from tasks.MultiAccountDelegation.task_name_resolver import TaskNameResolver


class DelegationInterval(str, Enum):
    SIX_HOURS = '六小时循环'
    COMPLETION_TIME = '完成时间循环'


class MultiAccountDelegationAccount(AccountInfo):
    """
    每个账号在“多账号式神委派”任务里的独立配置。

    这里保留了账号切换所需的基础信息，并额外增加了：
    - next_delegation_time：决定当前账号下一次是否需要委派
    - 各个委派任务的开关：每个账号都可以单独决定自己是否执行对应委派
    """

    next_delegation_time: DateTime = Field(
        default=DateTime.fromisoformat('2023-01-01 00:00:00'),
        description='每个账号下一次委派时间'
    )
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



class MultiAccountDelegationConfig(ConfigBase, extra='allow'):
    """
    多账号委派任务的公共配置。

    这里目前只保留和调度相关的公共字段：
    - account_count：生成多少个账号配置
    - delegation_interval：委派间隔策略
    """

    account_count: int = Field(default=1, ge=1, description='账号数量，决定下面会生成几组账号配置')
    delegation_interval: DelegationInterval = Field(
        default=DelegationInterval.SIX_HOURS,
        description='委派间隔：六小时循环|完成时间循环'
    )


class MultiAccountDelegation(ConfigBase, extra='allow'):
    """
    多账号式神委派任务的总配置对象。

    它负责保存：
    - 任务级调度信息
    - 公共委派策略
    - 每个账号自己的委派开关与下一次委派时间
    """

    scheduler: Scheduler = Field(default_factory=Scheduler)
    multi_account_delegation_config: MultiAccountDelegationConfig = Field(
        default_factory=MultiAccountDelegationConfig
    )
    account_list: list[MultiAccountDelegationAccount] = Field(default_factory=list)

    def update_account_next_delegation_time(self, account: MultiAccountDelegationAccount, next_time: datetime):
        for info in self.account_list:
            if info.character != account.character or info.svr != account.svr:
                continue
            info.next_delegation_time = next_time
            break

    def update_account_login_history(self, account: MultiAccountDelegationAccount):
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

        shared_config = v.get('multi_account_delegation_config')
        if not isinstance(shared_config, dict):
            shared_config = v.get('config') or v.get('shared_config') or {}
        if not isinstance(shared_config, dict):
            shared_config = {}

        account_count = shared_config.get('account_count', v.get('account_count', 1))
        try:
            account_count_value = int(account_count)
        except Exception:
            account_count_value = 1

        data = dict(v)
        data.setdefault('account_list', [])
        if 'multi_account_delegation_config' not in data:
            data['multi_account_delegation_config'] = dict(shared_config, account_count=account_count_value)
        elif isinstance(data['multi_account_delegation_config'], dict):
            data['multi_account_delegation_config'] = MultiAccountDelegationConfig(
                **data['multi_account_delegation_config']
            )

        for alias in ('config', 'shared_config'):
            data.pop(alias, None)

        remove_keys = []
        for key, value in data.items():
            if key == 'account_list' or 'account_list' not in key:
                continue
            try:
                item = MultiAccountDelegationAccount(**value)
                if item.is_valid():
                    data['account_list'].append(item)
                remove_keys.append(key)
            except Exception:
                pass

        while len(data['account_list']) < account_count_value:
            data['account_list'].append(MultiAccountDelegationAccount())

        for item in data['account_list']:
            if not hasattr(item, 'next_delegation_time') or not item.next_delegation_time:
                item.next_delegation_time = DateTime.fromisoformat('2023-01-01 00:00:00')

        for key in remove_keys:
            data.pop(key, None)

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
