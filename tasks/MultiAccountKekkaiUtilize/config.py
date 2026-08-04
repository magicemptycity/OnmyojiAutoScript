from datetime import datetime, time
from typing import Any, Dict

from pydantic import Field, model_serializer, model_validator

from deploy.logger import logger
from tasks.Component.SwitchAccount.switch_account_config import AccountInfo
from tasks.Component.config_base import ConfigBase, DateTime, Time
from tasks.Component.config_scheduler import Scheduler
from tasks.KekkaiUtilize.config import UtilizeRule, SelectFriendList
from tasks.Utils.config_enum import ShikigamiClass


class MultiAccountKekkaiUtilizeAccount(AccountInfo):
    """
    每个账号在“多账号蹭卡”任务中的独立配置。

    每个账号可以单独维护以下字段：
    - next_utilize_time: 用于判断当前账号是否需要执行蹭卡
    - utilize_rule / select_friend_list / shikigami_class / shikigami_order / utilize_harvest：
      这些字段均为账号单独配置，不与其他账号共享
    """

    next_utilize_time: DateTime = Field(
        default=DateTime.fromisoformat('2023-01-01 00:00:00'),
        description='每个账号下一次蹭卡时间'
    )
    utilize_rule: UtilizeRule = Field(default=UtilizeRule.DEFAULT, description='utilize_rule_help')
    select_friend_list: SelectFriendList = Field(default=SelectFriendList.SAME_SERVER, description='select_friend_list_help')
    auto_fill: bool = Field(default=False, description='auto_fill_help')
    shikigami_class: ShikigamiClass = Field(default=ShikigamiClass.N, description='shikigami_class_help')
    shikigami_order: int = Field(default=4, description='shikigami_order_help')
    utilize_harvest: bool = Field(default=True, description='是否领取寄养的收获')
    utilize_forbidden_time_enable: bool = Field(default=False, description='是否启用该账号的禁止蹭卡时间段')
    utilize_forbidden_time_start: Time = Field(default=time.fromisoformat('00:00:00'), description='账号禁止蹭卡时间段开始时间')
    utilize_forbidden_time_end: Time = Field(default=time.fromisoformat('00:00:00'), description='账号禁止蹭卡时间段结束时间')


class MultiAccountKekkaiUtilizeConfig(ConfigBase, extra='allow'):
    """
    多账号蹭卡任务的公共配置。

    这里把账号数量和所有“共享”的蹭卡策略配置放在一起，和 MultiAccountKekkaiActivation 的结构保持一致。
    """
    account_count: int = Field(default=1, ge=1, description='账号数量，决定下面会生成几组账号配置')
    box_exp_waste: bool = Field(default=True, description='box_exp_waste_help')
    box_exp_enable: bool = Field(default=True, description='box_exp_enable_help')
    box_ap_enable: bool = Field(default=True, description='box_ap_enable_help')
    harvest_guild_max_times: int = Field(default=3, description='收取寮资金或体力失败的最大尝试次数')
    utilize_enable: bool = Field(default=True, description='是否蹭卡，小号可以选择不蹭卡')
    public_forbid_time_enable: bool = Field(default=False, description='是否启用公共禁止蹭卡时间段')
    public_forbid_time_start: Time = Field(default=time.fromisoformat('00:00:00'), description='公共禁止蹭卡时间段开始时间')
    public_forbid_time_end: Time = Field(default=time.fromisoformat('00:00:00'), description='公共禁止蹭卡时间段结束时间')


class MultiAccountKekkaiUtilize(ConfigBase, extra='allow'):
    """
    多账号蹭卡任务的总配置对象。

    该对象负责：
    - 保存任务级调度信息
    - 保存账号列表与每个账号对应的下一次蹭卡时间
    - 提供账号下一次蹭卡时间的更新方法
    """

    scheduler: Scheduler = Field(default_factory=Scheduler)
    multi_account_kekkai_utilize_config: MultiAccountKekkaiUtilizeConfig = Field(
        default_factory=MultiAccountKekkaiUtilizeConfig
    )
    account_list: list[MultiAccountKekkaiUtilizeAccount] = Field(default_factory=list)

    def update_account_next_utilize_time(self, account: MultiAccountKekkaiUtilizeAccount, next_time: datetime):
        """
        更新指定账号的下一次蹭卡时间。

        该时间用于本任务调度判断：
        - 如果 next_utilize_time > 当前时间，则该账号本次不需要蹭卡
        - 如果 next_utilize_time <= 当前时间，则该账号本次需要执行蹭卡
        """
        for info in self.account_list:
            if info.character != account.character or info.svr != account.svr:
                continue
            info.next_utilize_time = next_time
            break

    def update_account_login_history(self, account: MultiAccountKekkaiUtilizeAccount):
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

        shared_config = v.get('multi_account_kekkai_utilize_config')
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
        if 'multi_account_kekkai_utilize_config' not in data:
            data['multi_account_kekkai_utilize_config'] = dict(shared_config, account_count=account_count_value)
        elif isinstance(data['multi_account_kekkai_utilize_config'], dict):
            data['multi_account_kekkai_utilize_config'] = MultiAccountKekkaiUtilizeConfig(
                **data['multi_account_kekkai_utilize_config']
            )

        for alias in ('config', 'shared_config'):
            data.pop(alias, None)

        remove_keys = []
        for key, value in data.items():
            if key == 'account_list' or 'account_list' not in key:
                continue
            try:
                item = MultiAccountKekkaiUtilizeAccount(**value)
                if item.is_valid():
                    data['account_list'].append(item)
                remove_keys.append(key)
            except Exception:
                pass

        while len(data['account_list']) < account_count_value:
            data['account_list'].append(MultiAccountKekkaiUtilizeAccount())

        for item in data['account_list']:
            if not hasattr(item, 'next_utilize_time') or not item.next_utilize_time:
                item.next_utilize_time = DateTime.fromisoformat('2023-01-01 00:00:00')

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
