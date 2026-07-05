import importlib
from datetime import datetime, timedelta
from pathlib import Path

from module.exception import RequestHumanTakeover, TaskEnd
from module.logger import logger
from tasks.Component.SwitchAccount.switch_account import SwitchAccount
from tasks.Component.SwitchAccount.assets import SwitchAccountAssets
from tasks.GameUi.game_ui import GameUi
from tasks.MultiAccountKekkaiUtilize.config import MultiAccountKekkaiUtilize


class ScriptTask(GameUi, SwitchAccountAssets):
    """
    多账号蹭卡任务执行器。

    该任务会：
    1. 读取所有账号的 next_utilize_time
    2. 仅对 next_utilize_time 小于等于当前任务执行时间的账号进行登录与蹭卡
    3. 挂完卡后读取 KekkaiUtilize 任务的下一次运行时间，并保存到当前账号的 next_utilize_time
    4. 当前任务的下一次运行时间取所有账号中最近的 next_utilize_time
    """

    fade_conf: MultiAccountKekkaiUtilize = None

    def run(self):
        self.fade_conf = self.config.multi_account_kekkai_utilize
        now = datetime.now()

        pending_accounts = []
        for account_info in self.fade_conf.account_list:
            if not account_info.is_valid():
                continue
            next_utilize_time = account_info.next_utilize_time
            if next_utilize_time and next_utilize_time > now:
                logger.info('%s-%s next utilize is %s, skip', account_info.character, account_info.svr, next_utilize_time)
                continue
            pending_accounts.append(account_info)

        if not pending_accounts:
            # 如果当前没有账号需要执行，则本任务按所有账号中最早的 next_utilize_time 重新调度
            next_run = self._get_next_run_time()
            self.set_next_run('MultiAccountKekkaiUtilize', target=next_run)
            raise TaskEnd('MultiAccountKekkaiUtilize')

        for account_info in pending_accounts:
            logger.info('start account %s-%s', account_info.character, account_info.svr)

            suc = SwitchAccount(self.config, self.device, account_info).switchAccount()
            if not suc:
                logger.warning('switch to %s-%s failed', account_info.character, account_info.svr)
                continue

            try:
                # 将当前账号的蹭卡配置临时写入全局 KekkaiUtilize 配置
                self._apply_account_utilize_config(account_info)
                task_obj = self.create_task_object('KekkaiUtilize', config=self.config, device=self.device)
                task_obj.run()
            except TaskEnd:
                logger.warning('%s-%s utilize task ended', account_info.character, account_info.svr)
            except RequestHumanTakeover:
                raise
            except Exception as e:
                logger.error('run KekkaiUtilize failed for %s-%s: %s', account_info.character, account_info.svr, e)
                self.set_next_run('MultiAccountKekkaiUtilize', success=False)
                break
            finally:
                # 无论执行结果如何，都恢复全局 KekkaiUtilize 配置，避免污染下一个账号
                self._restore_utilize_config()

            # 读取原始 KekkaiUtilize 任务的下一次运行时间，并保存到当前账号的 next_utilize_time
            next_utilize_time = self._get_kekkai_utilize_next_run_time()
            if next_utilize_time:
                self.fade_conf.update_account_next_utilize_time(account_info, next_utilize_time)
            else:
                # 如果无法读取原任务下一次运行时间，默认延后 1 小时再试
                self.fade_conf.update_account_next_utilize_time(account_info, datetime.now() + timedelta(hours=1))

            self.fade_conf.update_account_login_history(account_info)
            self.config.model.multi_account_kekkai_utilize = self.fade_conf
            self.config.save()

        next_run = self._get_next_run_time()
        self.set_next_run('MultiAccountKekkaiUtilize', target=next_run)
        raise TaskEnd('MultiAccountKekkaiUtilize')

    def _apply_account_utilize_config(self, account_info):
        """
        将当前账号的蹭卡策略临时写入全局 KekkaiUtilize 配置。

        仅覆写账号独立字段，保持其他 KekkaiUtilize 公共配置不变。
        """
        util_config = self.config.kekkai_utilize.utilize_config
        self._utilize_config_backup = {
            'utilize_rule': util_config.utilize_rule,
            'select_friend_list': util_config.select_friend_list,
            'shikigami_class': util_config.shikigami_class,
            'shikigami_order': util_config.shikigami_order,
            'utilize_harvest': util_config.utilize_harvest,
        }

        util_config.utilize_rule = account_info.utilize_rule
        util_config.select_friend_list = account_info.select_friend_list
        util_config.shikigami_class = account_info.shikigami_class
        util_config.shikigami_order = account_info.shikigami_order
        util_config.utilize_harvest = account_info.utilize_harvest
        self.config.save()

    def _restore_utilize_config(self):
        if not hasattr(self, '_utilize_config_backup'):
            return
        util_config = self.config.kekkai_utilize.utilize_config
        util_config.utilize_rule = self._utilize_config_backup['utilize_rule']
        util_config.select_friend_list = self._utilize_config_backup['select_friend_list']
        util_config.shikigami_class = self._utilize_config_backup['shikigami_class']
        util_config.shikigami_order = self._utilize_config_backup['shikigami_order']
        util_config.utilize_harvest = self._utilize_config_backup['utilize_harvest']
        self.config.save()

    def _get_next_run_time(self):
        """
        计算本任务的下一次调度时间。

        规则：取当前所有账号中 next_utilize_time 最早的时间。
        这样可以保证在最早到期的账号需要执行蹭卡时，本任务会及时唤醒。
        """
        next_times = [
            account.next_utilize_time
            for account in self.fade_conf.account_list
            if account.is_valid() and account.next_utilize_time
        ]
        if not next_times:
            return datetime.now() + timedelta(hours=1)
        return min(next_times)

    def _get_kekkai_utilize_next_run_time(self):
        scheduler = getattr(self.config.kekkai_utilize, 'scheduler', None)
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
