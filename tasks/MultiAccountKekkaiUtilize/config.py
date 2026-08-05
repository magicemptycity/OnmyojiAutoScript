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

    每个账号只保存与账号身份和调度相关的字段：
    - next_utilize_time: 用于判断当前账号是否需要执行蹭卡
    - enable_private_utilize_config: 是否启用该账号的私有结界蹭卡配置
    - enable_private_forbid_time: 是否启用该账号的私有禁止蹭卡时段
    """

    next_utilize_time: DateTime = Field(
        default=DateTime.fromisoformat('2023-01-01 00:00:00'),
        description='每个账号下一次蹭卡时间'
    )
    enable_private_utilize_config: bool = Field(default=False, description='是否启用私有结界蹭卡配置')
    enable_private_forbid_time: bool = Field(default=False, description='是否启用私有禁止蹭卡时段')


class MultiAccountKekkaiUtilizeConfig(ConfigBase, extra='allow'):
    """
    多账号蹭卡任务的公共结界蹭卡配置。

    这里保存所有共享的结界蹭卡参数。
    """
    utilize_rule: UtilizeRule = Field(default=UtilizeRule.DEFAULT, description='utilize_rule_help')
    select_friend_list: SelectFriendList = Field(default=SelectFriendList.SAME_SERVER, description='select_friend_list_help')
    auto_fill: bool = Field(default=False, description='auto_fill_help')
    shikigami_class: ShikigamiClass = Field(default=ShikigamiClass.N, description='shikigami_class_help')
    shikigami_order: int = Field(default=4, description='shikigami_order_help')
    harvest_guild_max_times: int = Field(default=3, description='harvest_guild_max_times_help')
    utilize_harvest: bool = Field(default=True, description='utilize_harvest_help')
    utilize_enable: bool = Field(default=True, description='utilize_enable_help')
    box_ap_enable: bool = Field(default=True)
    box_exp_enable: bool = Field(default=True)
    box_exp_waste: bool = Field(default=True, description='box_exp_waste_help')


class MultiAccountKekkaiUtilizeCountConfig(ConfigBase, extra='allow'):
    account_count: int = Field(default=1, ge=1, title='账号数量', description='账号数量，决定下面会生成几组账号配置')


class MultiAccountKekkaiUtilizeForbidConfig(ConfigBase, extra='allow'):
    public_forbid_time_enable: bool = Field(default=False, title='启用公共禁止蹭卡时段', description='是否启用公共禁止蹭卡时间段')
    public_forbid_time_start: Time = Field(default=time.fromisoformat('00:00:00'), title='禁止蹭卡开始时间', description='公共禁止蹭卡时间段开始时间')
    public_forbid_time_end: Time = Field(default=time.fromisoformat('00:00:00'), title='禁止蹭卡结束时间', description='公共禁止蹭卡时间段结束时间')


class MultiAccountKekkaiUtilizePrivateUtilizeConfig(ConfigBase):
    """
    账号私有结界蹭卡配置。

    该配置只在对应账号启用私有配置时生效。
    """
    utilize_rule: UtilizeRule = Field(default=UtilizeRule.DEFAULT, description='utilize_rule_help')
    select_friend_list: SelectFriendList = Field(default=SelectFriendList.SAME_SERVER, description='select_friend_list_help')
    auto_fill: bool = Field(default=False, description='auto_fill_help')
    shikigami_class: ShikigamiClass = Field(default=ShikigamiClass.N, description='shikigami_class_help')
    shikigami_order: int = Field(default=4, description='shikigami_order_help')
    harvest_guild_max_times: int = Field(default=3, description='harvest_guild_max_times_help')
    utilize_harvest: bool = Field(default=True, description='utilize_harvest_help')
    utilize_enable: bool = Field(default=True, description='utilize_enable_help')
    box_ap_enable: bool = Field(default=True)
    box_exp_enable: bool = Field(default=True)
    box_exp_waste: bool = Field(default=True, description='box_exp_waste_help')


class MultiAccountKekkaiUtilizePrivateForbidConfig(ConfigBase):
    """
    账号私有禁止蹭卡时段配置。

    该配置只在对应账号启用私有禁止时段时生效。
    """
    utilize_forbidden_time_enable: bool = Field(default=False, description='是否启用该账号的禁止蹭卡时间段')
    utilize_forbidden_time_start: Time = Field(default=time.fromisoformat('00:00:00'), description='账号禁止蹭卡时间段开始时间')
    utilize_forbidden_time_end: Time = Field(default=time.fromisoformat('00:00:00'), description='账号禁止蹭卡时间段结束时间')


class MultiAccountKekkaiUtilize(ConfigBase, extra='allow'):
    """
    多账号蹭卡任务的总配置对象。

    该对象负责：
    - 保存任务级调度信息
    - 保存账号列表与每个账号对应的下一次蹭卡时间
    - 提供账号下一次蹭卡时间的更新方法
    """

    scheduler: Scheduler = Field(default_factory=Scheduler)
    multi_account_kekkai_count_config: MultiAccountKekkaiUtilizeCountConfig = Field(
        default_factory=MultiAccountKekkaiUtilizeCountConfig,
        title='账号数量',
        description='账号数量，决定下面会生成几组账号配置'
    )
    multi_account_kekkai_forbid_config: MultiAccountKekkaiUtilizeForbidConfig = Field(
        default_factory=MultiAccountKekkaiUtilizeForbidConfig,
        title='公共禁止时段',
        description='公共禁止蹭卡时间段配置'
    )
    multi_account_kekkai_utilize_config: MultiAccountKekkaiUtilizeConfig = Field(
        default_factory=MultiAccountKekkaiUtilizeConfig,
        title='公共结界蹭卡配置',
        description='多账号蹭卡的共享结界蹭卡参数'
    )
    account_list: list[MultiAccountKekkaiUtilizeAccount] = Field(default_factory=list)
    private_utilize_config: list[MultiAccountKekkaiUtilizePrivateUtilizeConfig] = Field(default_factory=list)
    private_forbid_config: list[MultiAccountKekkaiUtilizePrivateForbidConfig] = Field(default_factory=list)

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

        data = dict(v)
        data.setdefault('account_list', [])
        data.setdefault('private_utilize_config', [])
        data.setdefault('private_forbid_config', [])

        shared_config = data.get('multi_account_kekkai_utilize_config')
        if not isinstance(shared_config, dict):
            shared_config = data.get('config') or data.get('shared_config') or {}
        if not isinstance(shared_config, dict):
            shared_config = {}

        count_config = data.get('multi_account_kekkai_count_config')
        if not isinstance(count_config, dict):
            count_config = {}

        forbid_config = data.get('multi_account_kekkai_forbid_config')
        if not isinstance(forbid_config, dict):
            forbid_config = {}

        # 兼容旧配置：旧配置可能把 account_count 和公共禁止时间段放在 shared_config 中。
        if 'account_count' not in count_config:
            count_config['account_count'] = data.get('account_count', shared_config.get('account_count', 1))

        if not forbid_config:
            forbid_config = {
                'public_forbid_time_enable': shared_config.get('public_forbid_time_enable', False),
                'public_forbid_time_start': shared_config.get('public_forbid_time_start', time.fromisoformat('00:00:00')),
                'public_forbid_time_end': shared_config.get('public_forbid_time_end', time.fromisoformat('00:00:00')),
            }

        if 'multi_account_kekkai_count_config' not in data or not isinstance(data['multi_account_kekkai_count_config'], MultiAccountKekkaiUtilizeCountConfig):
            data['multi_account_kekkai_count_config'] = MultiAccountKekkaiUtilizeCountConfig(**count_config)

        if 'multi_account_kekkai_forbid_config' not in data or not isinstance(data['multi_account_kekkai_forbid_config'], MultiAccountKekkaiUtilizeForbidConfig):
            data['multi_account_kekkai_forbid_config'] = MultiAccountKekkaiUtilizeForbidConfig(**forbid_config)

        if 'multi_account_kekkai_utilize_config' not in data:
            data['multi_account_kekkai_utilize_config'] = MultiAccountKekkaiUtilizeConfig()
        elif isinstance(data['multi_account_kekkai_utilize_config'], dict):
            data['multi_account_kekkai_utilize_config'] = MultiAccountKekkaiUtilizeConfig(
                **data['multi_account_kekkai_utilize_config']
            )

        for alias in ('config', 'shared_config', 'account_count', 'public_forbid_time_enable', 'public_forbid_time_start', 'public_forbid_time_end'):
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

        account_count_value = data['multi_account_kekkai_count_config'].account_count
        while len(data['account_list']) < account_count_value:
            data['account_list'].append(MultiAccountKekkaiUtilizeAccount())
        while len(data['private_utilize_config']) < account_count_value:
            data['private_utilize_config'].append(MultiAccountKekkaiUtilizePrivateUtilizeConfig())
        while len(data['private_forbid_config']) < account_count_value:
            data['private_forbid_config'].append(MultiAccountKekkaiUtilizePrivateForbidConfig())

        for index, account in enumerate(data['account_list']):
            if isinstance(account, dict):
                account_obj = MultiAccountKekkaiUtilizeAccount(**account)
            else:
                account_obj = account

            if getattr(account_obj, 'enable_private_utilize_config', False):
                private_value = data['private_utilize_config'][index]
                if isinstance(private_value, dict):
                    data['private_utilize_config'][index] = MultiAccountKekkaiUtilizePrivateUtilizeConfig(**private_value)
                elif private_value is None:
                    data['private_utilize_config'][index] = MultiAccountKekkaiUtilizePrivateUtilizeConfig()
            else:
                data['private_utilize_config'][index] = MultiAccountKekkaiUtilizePrivateUtilizeConfig()

            if getattr(account_obj, 'enable_private_forbid_time', False):
                private_value = data['private_forbid_config'][index]
                if isinstance(private_value, dict):
                    data['private_forbid_config'][index] = MultiAccountKekkaiUtilizePrivateForbidConfig(**private_value)
                elif private_value is None:
                    data['private_forbid_config'][index] = MultiAccountKekkaiUtilizePrivateForbidConfig()
            else:
                data['private_forbid_config'][index] = MultiAccountKekkaiUtilizePrivateForbidConfig()

        for index, item in enumerate(data['account_list']):
            if isinstance(item, dict):
                data['account_list'][index] = MultiAccountKekkaiUtilizeAccount(**item)
                item = data['account_list'][index]

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
            if key == 'account_list' and isinstance(value, list):
                for index, v in enumerate(value):
                    dumped = v_dump(v)
                    base_fields = list(AccountInfo.model_fields.keys()) + [
                        'next_utilize_time',
                        'enable_private_utilize_config',
                        'enable_private_forbid_time'
                    ]
                    filtered = {k: dumped.get(k) for k in base_fields if k in dumped}
                    data[f'{key}_{index + 1}'] = filtered

                    account_obj = v if isinstance(v, MultiAccountKekkaiUtilizeAccount) else MultiAccountKekkaiUtilizeAccount(**dumped)
                    if getattr(account_obj, 'enable_private_utilize_config', False):
                        private_cfg = self.private_utilize_config[index] if index < len(self.private_utilize_config) else MultiAccountKekkaiUtilizePrivateUtilizeConfig()
                        private_dump = v_dump(private_cfg)
                        data[f'private_utilize_config_{index + 1}'] = private_dump
                    if getattr(account_obj, 'enable_private_forbid_time', False):
                        private_cfg = self.private_forbid_config[index] if index < len(self.private_forbid_config) else MultiAccountKekkaiUtilizePrivateForbidConfig()
                        private_dump = v_dump(private_cfg)
                        data[f'private_forbid_config_{index + 1}'] = private_dump
                continue

            if key in ('private_utilize_config', 'private_forbid_config'):
                continue

            if isinstance(value, list):
                for index, v in enumerate(value):
                    data[f'{key}_{index + 1}'] = v_dump(v)
            else:
                data[key] = v_dump(value)
        return data
