from datetime import datetime
from typing import Any, Dict

from pydantic import Field, ValidationError, model_serializer, model_validator

from deploy.logger import logger
from tasks.Component.SwitchAccount.switch_account_config import AccountInfo
from tasks.Component.config_base import ConfigBase, DateTime
from tasks.Component.config_scheduler import Scheduler
from tasks.KekkaiActivation.config import CardType, CardStar


class MultiAccountKekkaiActivationAccount(AccountInfo):
    """
    每个账号在“多账号挂卡”任务中的独立配置。

    继承自通用账号信息，并额外增加了：
    - 下一次挂卡时间：决定这个账号何时需要再次挂卡
    - 挂卡相关参数：每个账号都可以单独维护自己的挂卡策略
    """
    next_activation_time: DateTime = Field(
        default=DateTime.fromisoformat('2023-01-01 00:00:00'),
        description='每个账号下一次挂卡时间'
    )
    card_type: CardType = Field(default=CardType.TAIKO, description='card_rule_help')
    card_star: CardStar = Field(default=CardStar.SIX, description='6星：六星及以下、5星：五星及以下')
    swipe_retry_limit: int = Field(default=10, description='最多滑动X次后触发未找到符合条件的卡')
    min_taiko_num: int = Field(default=8, description='挂卡太鼓每小时最少收益,低于则不挂卡')
    min_fish_num: int = Field(default=16, description='挂卡斗鱼每小时最少收益,低于则不挂卡')
    card_not_found_count: int = Field(default=0, description='未发现卡次数')


class MultiAccountKekkaiActivationConfig(ConfigBase, extra='allow'):
    """
    多账号挂卡任务的公共配置。

    这里的字段主要控制：
    - 账号数量：到底要生成几组账号配置
    - 是否跳过今日已执行过登录/挂卡的账号
    - 哪些挂卡参数是“共享”的，哪些是“按账号独立”的
    """
    account_count: int = Field(default=1, ge=1, description='账号数量，决定下面会生成几组账号配置')
    skip_if_logged_today: bool = Field(default=True, description='如果上次登录时间为今天，则跳过该账号的登录与任务执行')
    shared_activation_config: bool = Field(default=True, description='是否共享结界挂卡公共配置')
    shared_card_type: bool = Field(default=True, description='是否共享卡类型')
    shared_card_star: bool = Field(default=True, description='是否共享卡星级')
    shared_swipe_retry_limit: bool = Field(default=True, description='是否共享滑动次数')
    shared_min_taiko_num: bool = Field(default=True, description='是否共享太鼓最低收益')
    shared_min_fish_num: bool = Field(default=True, description='是否共享斗鱼最低收益')
    shared_card_not_found_count: bool = Field(default=False, description='是否共享卡未检出计数')


class MultiAccountKekkaiActivation(ConfigBase):
    """
    多账号挂卡任务的总配置对象。

    这个对象负责保存整个任务的调度器信息、公共参数，以及所有账号级别的挂卡配置。
    """
    scheduler: Scheduler = Field(default_factory=Scheduler)
    multi_account_kekkai_activation_config: MultiAccountKekkaiActivationConfig = Field(
        default_factory=MultiAccountKekkaiActivationConfig
    )
    account_list: list[MultiAccountKekkaiActivationAccount] = Field(default_factory=list)

    def update_account_next_activation_time(self, account: MultiAccountKekkaiActivationAccount, next_time: datetime):
        for info in self.account_list:
            if info.character != account.character or info.svr != account.svr:
                continue
            info.next_activation_time = next_time
            break

    def update_account_login_history(self, account: MultiAccountKekkaiActivationAccount):
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

        account_count = v.get('multi_account_kekkai_activation_config', {}).get('account_count', 1)
        data = dict(v)
        data.setdefault('account_list', [])

        remove_keys = []
        for key, value in data.items():
            if key == 'account_list' or 'account_list' not in key:
                continue
            try:
                item = MultiAccountKekkaiActivationAccount(**value)
                if item.is_valid():
                    data['account_list'].append(item)
                remove_keys.append(key)
            except (ValidationError, TypeError):
                pass

        while len(data['account_list']) < account_count:
            data['account_list'].append(MultiAccountKekkaiActivationAccount())

        for item in data['account_list']:
            if not hasattr(item, 'next_activation_time') or not item.next_activation_time:
                item.next_activation_time = DateTime.fromisoformat('2023-01-01 00:00:00')

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
