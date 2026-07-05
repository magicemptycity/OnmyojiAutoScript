import importlib
from datetime import datetime, timedelta
from pathlib import Path

from module.exception import RequestHumanTakeover, TaskEnd
from module.logger import logger
from tasks.Component.SwitchAccount.switch_account import SwitchAccount
from tasks.GameUi.game_ui import GameUi
from tasks.KekkaiActivation.config import CardType
from tasks.MultiAccountKekkaiActivation.assets import MultiAccountKekkaiActivationAssets
from tasks.MultiAccountKekkaiActivation.config import (
    MultiAccountKekkaiActivation,
)
from tasks.Component.SwitchAccount.assets import SwitchAccountAssets


class ScriptTask(GameUi, MultiAccountKekkaiActivationAssets, SwitchAccountAssets):
    """
    多账号挂卡任务。

    这个任务不直接改动原有的“结界挂卡”逻辑，而是作为一个外层调度器：
    1. 依次切换到每个账号；
    2. 将该账号对应的挂卡配置临时应用到结界挂卡任务；
    3. 调用原有的结界挂卡任务完成实际挂卡；
    4. 读取结界挂卡任务在执行完后的下一次调度时间，并保存到当前账号的“下一次挂卡时间”中。
    """
    fade_conf: MultiAccountKekkaiActivation = None

    def run(self):
        # 读取当前任务的配置对象，后续所有账号级别的挂卡时间、账号信息都会保存在这里
        self.fade_conf = self.config.multi_account_kekkai_activation
        now = datetime.now()

        # 先筛选出当前需要执行挂卡的账号。
        # 逻辑是：如果账号的“下一次挂卡时间”还没有到，就跳过；只有到了或超过这一时间才执行。
        pending_accounts = []
        for account_info in self.fade_conf.account_list:
            if not account_info.is_valid():
                # 账号信息不完整，跳过，避免后续切换时出现异常
                continue

            if self.fade_conf.multi_account_kekkai_activation_config.skip_if_logged_today:
                last_complete_time = account_info.last_complete_time
                if last_complete_time.date() == now.date():
                    # 如果今天已经执行过登录/挂卡流程，则跳过，避免重复登录浪费时间
                    logger.warning('%s-%s skipped because last_complete_time is today: %s', account_info.character, account_info.svr, last_complete_time)
                    continue

            next_activation_time = account_info.next_activation_time
            if next_activation_time and next_activation_time > now:
                # 还没到这个账号的下一次挂卡时间，当前轮次不处理这个账号
                logger.info('%s-%s next activation is %s, skip', account_info.character, account_info.svr, next_activation_time)
                continue

            pending_accounts.append(account_info)

        # 如果没有任何账号需要执行，就把本任务的下一次运行时间设置为所有账号中最近的那个挂卡时间
        if not pending_accounts:
            self.set_next_run('MultiAccountKekkaiActivation', target=self._get_next_run_time())
            raise TaskEnd('MultiAccountKekkaiActivation')

        # 依次为每个需要挂卡的账号执行流程
        for account_info in pending_accounts:
            logger.info('start account %s-%s', account_info.character, account_info.svr)

            # 先切换到目标账号
            suc = SwitchAccount(self.config, self.device, account_info).switchAccount()
            if not suc:
                logger.warning('switch to %s-%s failed', account_info.character, account_info.svr)
                continue

            try:
                # 把当前账号对应的挂卡配置临时应用到“结界挂卡”任务中
                self._apply_account_activation_config(account_info)

                # 调用原有的结界挂卡任务完成真实挂卡动作
                task_obj = self.create_task_object('KekkaiActivation', config=self.config, device=self.device)
                task_obj.run()
            except TaskEnd:
                logger.warning('%s-%s activation task ended', account_info.character, account_info.svr)
            except RequestHumanTakeover:
                raise
            except Exception as e:
                logger.error('run KekkaiActivation failed for %s-%s: %s', account_info.character, account_info.svr, e)
                self.set_next_run('MultiAccountKekkaiActivation', success=False)
                break
            finally:
                # 无论挂卡成功与否，都要把临时改动的公共挂卡配置恢复，避免污染后续账号
                self._restore_activation_config()

            # 读取结界挂卡任务执行完后的下一次调度时间，并记录给当前账号
            next_activation_time = self._get_kekkai_activation_next_run_time()
            if next_activation_time:
                self.fade_conf.update_account_next_activation_time(account_info, next_activation_time)
            else:
                # 如果原任务没有给出明确下一次时间，就给一个默认的1小时后重试时间
                self.fade_conf.update_account_next_activation_time(account_info, datetime.now() + timedelta(hours=1))

            # 记录这次登录/执行的完成时间，供后续“今日是否跳过”的逻辑使用
            self.fade_conf.update_account_login_history(account_info)
            self.config.model.multi_account_kekkai_activation = self.fade_conf
            self.config.save()

        # 所有账号处理完后，设置本任务的下一次运行时间为所有账号中最早的那次挂卡时间
        next_run = self._get_next_run_time()
        self.set_next_run('MultiAccountKekkaiActivation', target=next_run)
        raise TaskEnd('MultiAccountKekkaiActivation')

    def _apply_account_activation_config(self, account_info):
        """
        把当前账号的挂卡配置临时写入结界挂卡任务中。

        这里保留了“共享配置”和“账号独立配置”两种模式。
        对于你要求的“部分字段公有化，部分字段按账号单独配置”，
        只需要在配置项中把对应共享开关开启或关闭即可。
        """
        activation_config = self.config.kekkai_activation.activation_config
        shared_config = self.fade_conf.multi_account_kekkai_activation_config

        # 先保存一份原始配置，后面执行完后恢复，避免影响下一个账号
        self._activation_config_backup = {
            'card_type': activation_config.card_type,
            'card_star': activation_config.card_star,
            'swipe_retry_limit': activation_config.swipe_retry_limit,
            'min_taiko_num': activation_config.min_taiko_num,
            'min_fish_num': activation_config.min_fish_num,
            'card_not_found_count': activation_config.card_not_found_count,
        }

        # 如果当前字段被配置为“共享”，则继续使用当前全局配置；否则使用当前账号自己的值
        activation_config.card_type = activation_config.card_type if shared_config.shared_card_type else account_info.card_type
        activation_config.card_star = activation_config.card_star if shared_config.shared_card_star else account_info.card_star
        activation_config.swipe_retry_limit = activation_config.swipe_retry_limit if shared_config.shared_swipe_retry_limit else account_info.swipe_retry_limit
        activation_config.min_taiko_num = activation_config.min_taiko_num if shared_config.shared_min_taiko_num else account_info.min_taiko_num
        activation_config.min_fish_num = activation_config.min_fish_num if shared_config.shared_min_fish_num else account_info.min_fish_num
        activation_config.card_not_found_count = activation_config.card_not_found_count if shared_config.shared_card_not_found_count else account_info.card_not_found_count
        self.config.save()

    def _restore_activation_config(self):
        """
        恢复结界挂卡任务的原始公共配置，避免一个账号执行完后把配置污染给下一个账号。
        """
        if not hasattr(self, '_activation_config_backup'):
            return
        activation_config = self.config.kekkai_activation.activation_config
        activation_config.card_type = self._activation_config_backup['card_type']
        activation_config.card_star = self._activation_config_backup['card_star']
        activation_config.swipe_retry_limit = self._activation_config_backup['swipe_retry_limit']
        activation_config.min_taiko_num = self._activation_config_backup['min_taiko_num']
        activation_config.min_fish_num = self._activation_config_backup['min_fish_num']
        activation_config.card_not_found_count = self._activation_config_backup['card_not_found_count']
        self.config.save()

    def _get_next_run_time(self) -> datetime:
        """
        计算本任务下一次应该执行的时间。

        规则是：取所有账号中“下一次挂卡时间”最早的那个。
        这样可以确保任务在最需要挂卡的账号上尽快触发。
        """
        next_times = [
            account.next_activation_time
            for account in self.fade_conf.account_list
            if account.is_valid() and account.next_activation_time
        ]
        if not next_times:
            return datetime.now() + timedelta(hours=1)
        next_times = [dt for dt in next_times if dt]
        return min(next_times)

    def _get_kekkai_activation_next_run_time(self) -> datetime | None:
        """
        从原有的“结界挂卡”任务中读取它的下一次调度时间。

        这里不去改动原任务逻辑，而是把原任务执行后的调度结果读出来，
        作为当前账号下一次挂卡时间的依据。
        """
        scheduler = getattr(self.config.kekkai_activation, 'scheduler', None)
        if scheduler is None:
            return None
        next_run = getattr(scheduler, 'next_run', None)
        if isinstance(next_run, datetime):
            return next_run
        return None

    def create_task_object(self, task_name: str, **kwargs):
        module_path = str(Path.cwd() / 'tasks' / task_name / 'script_task.py')
        spec = importlib.util.spec_from_file_location('script_task', module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.ScriptTask(**kwargs)
