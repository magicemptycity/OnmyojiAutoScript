from datetime import datetime
from typing import Any, Dict

from pydantic import Field, ValidationError, model_serializer, model_validator

from deploy.logger import logger
from tasks.Component.SwitchAccount.switch_account_config import AccountInfo
from tasks.Component.config_base import ConfigBase, dynamic_hide
from tasks.Component.config_scheduler import Scheduler
from tasks.AreaBoss.config_boss import AreaBossFloor


class MultiAccountAreaBossAccount(AccountInfo):
    enable_private_config: bool = Field(default=False, description='是否启用私有配置')


class MultiAccountAreaBossPrivateConfig(ConfigBase):
    boss_number: int = Field(default=3, ge=1, le=3, description='默认为3 可选[1-3], 当你设置为三时默认你拥有全部的挑战资格，会挑战热门的前三个，\n'
                                         '如果不是请将你可以挑战的boss进行收藏')
    boss_reward: bool = Field(default=False, description='boss_reward_help')
    reward_floor: AreaBossFloor = Field(default=AreaBossFloor.ONE, description='reward_floor_help')
    use_collect: bool = Field(default=False, description='use_collect_help')
    attack_60: bool = Field(default=False, description='没有开启极是否拉到60级进行攻打')
    lock_team: bool = Field(default=False, description='lock_team_enable_help')
    enable_switch_team: bool = Field(default=False, description='preset_enable_help')
    enable_switch_soul: bool = Field(default=False, description='switch_soul_config')
    soul_team_group: str = Field(default='-1,-1', description='soul_team_group_help')


class MultiAccountAreaBossCommonConfig(MultiAccountAreaBossPrivateConfig):
    pass


class MultiAccountAreaBossConfig(ConfigBase, extra='allow'):
    account_count: int = Field(default=1, ge=1, description='账号数量，决定下面会生成几组账号配置')


class MultiAccountAreaBoss(ConfigBase):
    scheduler: Scheduler = Field(default_factory=Scheduler)
    multi_account_area_boss_config: MultiAccountAreaBossConfig = Field(default_factory=MultiAccountAreaBossConfig)
    common_config: MultiAccountAreaBossCommonConfig = Field(default_factory=MultiAccountAreaBossCommonConfig)
    account_list: list[MultiAccountAreaBossAccount] = Field(default_factory=list)
    private_config: list[MultiAccountAreaBossPrivateConfig] = Field(default_factory=list)

    def update_account_login_history(self, account: MultiAccountAreaBossAccount):
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

        account_count = v.get('multi_account_area_boss_config', {}).get('account_count', 1)
        try:
            account_count = int(account_count)
        except Exception:
            account_count = 1

        data = dict(v)
        data.setdefault('account_list', [])
        data.setdefault('private_config', [])

        remove_keys = []
        for key, value in data.items():
            if key == 'account_list' or 'account_list' not in key:
                continue
            try:
                item = MultiAccountAreaBossAccount(**value)
                if item.is_valid():
                    data['account_list'].append(item)
                remove_keys.append(key)
            except (ValidationError, TypeError):
                pass

        for index, account in enumerate(data['account_list']):
            if isinstance(account, dict):
                account_obj = MultiAccountAreaBossAccount(**account)
            else:
                account_obj = account

            while len(data['private_config']) <= index:
                data['private_config'].append(MultiAccountAreaBossPrivateConfig())

            if getattr(account_obj, 'enable_private_config', False):
                private_value = data['private_config'][index]
                if isinstance(private_value, dict):
                    data['private_config'][index] = MultiAccountAreaBossPrivateConfig(**private_value)
                elif private_value is None:
                    data['private_config'][index] = MultiAccountAreaBossPrivateConfig()
            else:
                data['private_config'][index] = MultiAccountAreaBossPrivateConfig()

        for key in remove_keys:
            data.pop(key, None)

        while len(data['account_list']) < account_count:
            data['account_list'].append(MultiAccountAreaBossAccount())
        while len(data['private_config']) < account_count:
            data['private_config'].append(MultiAccountAreaBossPrivateConfig())

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
                    base_fields = list(AccountInfo.model_fields.keys()) + ['enable_private_config']
                    filtered = {k: dumped.get(k) for k in base_fields if k in dumped}
                    data[f'{key}_{index + 1}'] = filtered

                    if getattr(v, 'enable_private_config', False):
                        private_cfg = self.private_config[index] if index < len(self.private_config) else MultiAccountAreaBossPrivateConfig()
                        private_dump = v_dump(private_cfg)
                        data[f'private_config_{index + 1}'] = private_dump
                continue

            if key == 'private_config':
                continue

            if isinstance(value, list):
                for index, v in enumerate(value):
                    data[f'{key}_{index + 1}'] = v_dump(v)
            else:
                data[key] = v_dump(value)
        return data
