import copy
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
    多账号结界蹭卡的外层调度器。

    这个脚本不直接完成实际的蹭卡点击、识图和结算动作，而是把“多账号轮询调度”这件事收拢到这里：
    1. 先读取当前多账号蹭卡配置中的账号列表和每个账号的 next_utilize_time；
    2. 只对已经达到或超过调度时间的账号执行蹭卡流程，避免所有账号同时进入当前轮次；
    3. 对每个待执行账号，先切换到该账号，再把账号对应的蹭卡配置临时覆盖到内层 KekkaiUtilize 任务上；
    4. 调用内层任务真正执行蹭卡动作，并读取它在执行后更新的下一次调度时间；
    5. 把该时间写回当前账号的 next_utilize_time，并把外层任务的下一次调度时间重新计算为所有账号里最早需要执行的那一刻。

    这种设计的关键点在于“外层负责调度，内层负责执行”，中间通过临时配置覆盖和恢复来隔离各账号的状态。
    如果不做配置和 scheduler 的回收，后一个账号会继承前一个账号的临时修改，甚至拿到被内层任务污染后的 next_run，
    导致后来账号的蹭卡时机被错误覆盖，或者出现重复执行和漏调度的问题。
    """

    fade_conf: MultiAccountKekkaiUtilize = None

    def run(self):
        # 读取当前多账号蹭卡任务的配置对象。这里保存的是外层任务本身的账号列表、每个账号的 next_utilize_time，以及当前轮次要回写的调度状态。
        # 后续每个账号执行完后，都会把下一次蹭卡时间写回这个对象，因此它是外层调度链路的核心上下文。
        self.fade_conf = self.config.multi_account_kekkai_utilize
        now = datetime.now()

        # 先筛选出当前轮次真正需要执行蹭卡的账号。
        # 规则是：只有当账号的 next_utilize_time 已经到达当前时间或已经超过它时，才会进入待执行队列；
        # 如果还没有到预定时间，就视为“当前轮次不处理”，直接跳过，避免把所有账号一起拉出来执行。
        pending_accounts = []
        for account_info in self.fade_conf.account_list:
            if not account_info.is_valid():
                # 账号信息不完整就跳过，避免后续切换账号时出现异常
                continue

            next_utilize_time = account_info.next_utilize_time
            if next_utilize_time and next_utilize_time > now:
                logger.info('%s-%s next utilize is %s, skip', account_info.character, account_info.svr, next_utilize_time)
                continue

            pending_accounts.append(account_info)

        # 如果当前没有任何账号需要执行，说明此时没有任何账号到达蹭卡时机。
        # 这时就把外层任务的下一次调度时间设置为所有账号里最早需要执行的那一次蹭卡时间，然后结束本次轮次。
        if not pending_accounts:
            next_run = self._get_next_run_time()
            self.set_next_run('MultiAccountKekkaiUtilize', target=next_run)
            raise TaskEnd('MultiAccountKekkaiUtilize')

        # 对每个待执行账号，都遵循“切换账号 -> 临时覆盖配置 -> 调用内层任务 -> 读取下一次调度 -> 恢复配置 -> 更新账号状态”这条链路。
        # 这样可以保证前一个账号的临时配置不会污染后一个账号，也能把内层任务执行后生成的下一次蹭卡时间正确回写到当前账号上。
        for account_info in pending_accounts:
            logger.info('start account %s-%s', account_info.character, account_info.svr)

            # 先切换到目标账号，确保后续的蹭卡动作是在正确账号上执行
            suc = SwitchAccount(self.config, self.device, account_info).switchAccount()
            if not suc:
                logger.warning('switch to %s-%s failed', account_info.character, account_info.svr)
                continue

            try:
                # 把当前账号对应的蹭卡配置临时应用到全局的 KekkaiUtilize 任务中
                # 这样内层任务运行时就会使用当前账号的配置，而不是污染给其他账号
                self._apply_account_utilize_config(account_info)

                # 创建并运行原始的 KekkaiUtilize 任务，完成真实蹭卡动作
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
                # 这里必须先读取内层任务执行完后的 next_run，再恢复临时配置。
                # 因为恢复配置后，原任务的调度结果就不再可读了，所以要先拿到它。
                next_utilize_time = self._get_kekkai_utilize_next_run_time()
                self._restore_utilize_config()

            # 把内层任务执行完后的下一次运行时间保存到当前账号的 next_utilize_time 中。
            # 如果没有拿到有效值，就保留当前账号的旧计划，避免把它错误地改成一个小时后。
            if next_utilize_time:
                self.fade_conf.update_account_next_utilize_time(account_info, next_utilize_time)
            else:
                # 如果无法读取原任务下一次运行时间，默认延后 1 小时再试
                self.fade_conf.update_account_next_utilize_time(account_info, datetime.now() + timedelta(hours=1))

            # 记录这次账号执行过的登录历史，方便后续判断是否重复执行或跳过
            self.fade_conf.update_account_login_history(account_info)
            self.config.model.multi_account_kekkai_utilize = self.fade_conf
            self.config.save()

        # 所有账号处理完后，重新计算本外层任务的下一次调度时间。
        # 规则仍然是取所有账号中最早的 next_utilize_time。
        next_run = self._get_next_run_time()
        self.set_next_run('MultiAccountKekkaiUtilize', target=next_run)
        raise TaskEnd('MultiAccountKekkaiUtilize')

    def _apply_account_utilize_config(self, account_info):
        """
        将当前账号的蹭卡策略临时写入全局 KekkaiUtilize 配置。

        这一步并不是真正执行蹭卡，而是把当前账号的“专属配置”和外层多账号任务的“公共配置”融合后，
        临时覆盖到内层 KekkaiUtilize 任务上，让内层任务在本次执行中使用当前账号的策略。
        具体做法是：
        - 账号自己的字段，如 utilize_rule、select_friend_list、auto_fill 等，直接使用当前账号的配置；
        - 共享字段，如 harvest_guild_max_times、utilize_enable、guild_ap_enable 等，从多账号蹭卡任务的公共配置中读取；
        - 先把原来的配置和 scheduler 状态完整备份下来，避免后续恢复时丢失上下文；
        - 执行完内层任务后，在 finally 中把这些临时改动恢复，确保下一个账号不会继承前一个账号的状态。
        """
        util_config = self.config.kekkai_utilize.utilize_config
        shared_config = getattr(getattr(self, 'fade_conf', None), 'multi_account_kekkai_utilize_config', None)
        if shared_config is None:
            shared_config = self.config.multi_account_kekkai_utilize.multi_account_kekkai_utilize_config

        # 先把原来的配置完整备份下来，后面执行完后再恢复。
        # 这样不会因为当前账号的临时配置把下一个账号的配置也改坏。
        self._utilize_config_backup = {
            'utilize_rule': util_config.utilize_rule,
            'select_friend_list': util_config.select_friend_list,
            'auto_fill': util_config.auto_fill,
            'shikigami_class': util_config.shikigami_class,
            'shikigami_order': util_config.shikigami_order,
            'harvest_guild_max_times': util_config.harvest_guild_max_times,
            'utilize_harvest': util_config.utilize_harvest,
            'utilize_enable': util_config.utilize_enable,
            'guild_ap_enable': util_config.guild_ap_enable,
            'guild_assets_enable': util_config.guild_assets_enable,
            'box_ap_enable': util_config.box_ap_enable,
            'box_exp_enable': util_config.box_exp_enable,
            'box_exp_waste': util_config.box_exp_waste,
        }

        # 同时把 KekkaiUtilize 的 scheduler 也备份下来。
        # 因为内层任务会修改它的 next_run，必须在恢复时一起把它还原，
        # 否则下一个账号会拿到被污染后的调度状态。
        self._kekkai_scheduler_backup = copy.deepcopy(self.config.kekkai_utilize.scheduler)

        # 将当前账号的配置项写入全局配置中。这里的字段分成两类：
        # - 当前账号独占的字段：使用 account_info 中的值；
        # - 共享字段：使用多账号蹭卡任务层面的 shared_config。
        util_config.utilize_rule = account_info.utilize_rule
        util_config.select_friend_list = account_info.select_friend_list
        util_config.auto_fill = account_info.auto_fill
        util_config.shikigami_class = account_info.shikigami_class
        util_config.shikigami_order = account_info.shikigami_order
        util_config.harvest_guild_max_times = shared_config.harvest_guild_max_times
        util_config.utilize_harvest = account_info.utilize_harvest
        util_config.utilize_enable = shared_config.utilize_enable
        util_config.guild_ap_enable = shared_config.guild_ap_enable
        util_config.guild_assets_enable = shared_config.guild_assets_enable
        util_config.box_ap_enable = shared_config.box_ap_enable
        util_config.box_exp_enable = shared_config.box_exp_enable
        util_config.box_exp_waste = shared_config.box_exp_waste
        self.config.save()

    def _restore_utilize_config(self):
        """
        恢复 KekkaiUtilize 的原始配置和调度状态。

        每个账号执行完一次蹭卡后，都需要把之前临时覆盖进去的配置和 scheduler 状态恢复到执行前的样子。
        这是为了避免内层任务在本轮执行中对全局配置造成污染：
        - 如果只恢复配置，不恢复 scheduler，那么下一个账号仍可能拿到被内层任务改写过的 next_run；
        - 如果只恢复 scheduler，不恢复配置，那么下一个账号仍会继承前一个账号的临时策略；
        - 只有两者一起恢复，外层任务才能保证后续账号拿到的是干净的初始状态。
        """
        if not hasattr(self, '_utilize_config_backup'):
            return
        util_config = self.config.kekkai_utilize.utilize_config
        util_config.utilize_rule = self._utilize_config_backup['utilize_rule']
        util_config.select_friend_list = self._utilize_config_backup['select_friend_list']
        util_config.auto_fill = self._utilize_config_backup['auto_fill']
        util_config.shikigami_class = self._utilize_config_backup['shikigami_class']
        util_config.shikigami_order = self._utilize_config_backup['shikigami_order']
        util_config.harvest_guild_max_times = self._utilize_config_backup['harvest_guild_max_times']
        util_config.utilize_harvest = self._utilize_config_backup['utilize_harvest']
        util_config.utilize_enable = self._utilize_config_backup['utilize_enable']
        util_config.guild_ap_enable = self._utilize_config_backup['guild_ap_enable']
        util_config.guild_assets_enable = self._utilize_config_backup['guild_assets_enable']
        util_config.box_ap_enable = self._utilize_config_backup['box_ap_enable']
        util_config.box_exp_enable = self._utilize_config_backup['box_exp_enable']
        util_config.box_exp_waste = self._utilize_config_backup['box_exp_waste']

        # 把备份的 scheduler 对象重新写回去，恢复内层任务执行前的调度状态。
        if hasattr(self, '_kekkai_scheduler_backup'):
            self.config.kekkai_utilize.scheduler = self._kekkai_scheduler_backup

        self.config.save()

    def _get_next_run_time(self):
        """
        计算外层多账号蹭卡任务的下一次调度时间。

        规则是：从所有有效账号中取出每个账号当前维护的 next_utilize_time，挑出其中最早的那一个。
        这样可以确保只要有任意一个账号已经到达蹭卡时间点，外层任务就会在最早需要执行的时间点重新被唤醒。
        如果当前没有任何有效账号记录到 next_utilize_time，就回退到一个默认的 1 小时后，避免外层任务失去可调度的时机。
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
        """
        读取内层 KekkaiUtilize 任务执行完后的下一次调度时间。

        这个值会被保存到当前账号的 next_utilize_time 中，作为这个账号下一次蹭卡的计划时间。
        之所以优先从内层任务读取，而不是直接生成一个固定间隔，是因为它来自内层任务真实执行后更新的 scheduler 状态，
        更能反映当前任务的真实调度结果。只有当内层任务没有给出有效 next_run 时，外层才会回退到默认策略。
        """
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
