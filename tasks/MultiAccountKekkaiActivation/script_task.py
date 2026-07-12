import copy
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
    多账号结界挂卡的外层调度器。

    这个脚本本身不负责点击、识图或执行实际挂卡动作，而是把“多账号轮询调度”这个职责收拢到这里：
    1. 先从多账号配置中读取每个账号的挂卡计划时间；
    2. 只对已经达到或超过 next_activation_time 的账号进入执行队列，避免所有账号在同一轮被一起处理；
    3. 对每个待执行账号，先切换到该账号，再把该账号对应的挂卡配置临时覆盖到内层 KekkaiActivation 任务上；
    4. 调用内层任务真正完成挂卡动作，并读取内层任务执行后生成的下一次运行时间；
    5. 将这个时间回写到当前账号的 next_activation_time，同时把外层任务自己的下一次调度时间更新为所有账号里最早需要执行的那一刻。

    这里之所以要采用“临时覆盖配置 + finally 里还原”的方式，是因为同一个运行实例会被多个账号复用。
    如果某个账号执行完后没有把临时改动恢复掉，下一个账号就会继承前一个账号的配置，甚至把内层任务的 scheduler 状态一并污染掉。
    这会导致后续账号的挂卡逻辑出现错位、重复触发，或者把下一次执行时间误写成错误的值。
    """
    fade_conf: MultiAccountKekkaiActivation = None

    def run(self):
        # 读取当前多账号挂卡任务的配置对象。这里保存的是外层任务自己的账号列表、每个账号的下一次挂卡时间以及相关账号信息。
        # 后续所有账号级别的调度状态都会在这个对象上更新，因此它是这条外层调度链路的核心上下文。
        self.fade_conf = self.config.multi_account_kekkai_activation
        now = datetime.now()

        # 先筛选出当前轮次真正需要执行挂卡的账号。
        # 规则是：只有当账号的 next_activation_time 到达当前时间，或者已经过去时，才会进入待执行队列；
        # 如果账号还没有到达预定时间，就视为“当前轮次不处理”，直接跳过。
        # 这样做的好处是，外层任务不会把所有账号一次性全部拉出来执行，而是按时间推进来执行最需要触发的那部分账号。
        pending_accounts = []
        for account_info in self.fade_conf.account_list:
            if not account_info.is_valid():
                # 账号信息不完整，跳过，避免后续切换时出现异常
                continue

            next_activation_time = account_info.next_activation_time
            if next_activation_time and next_activation_time > now:
                # 还没到这个账号的下一次挂卡时间，当前轮次不处理这个账号
                logger.info('%s-%s next activation is %s, skip', account_info.character, account_info.svr, next_activation_time)
                continue

            pending_accounts.append(account_info)

        # 如果当前轮次没有任何账号需要执行，说明此时没有任何账号到达挂卡时机。
        # 这时就把外层任务的下一次调度时间设置为所有账号中最早需要执行的那个挂卡时间，然后直接结束本次循环。
        if not pending_accounts:
            self.set_next_run('MultiAccountKekkaiActivation', target=self._get_next_run_time())
            raise TaskEnd('MultiAccountKekkaiActivation')

        # 对每个待执行账号，按“切换账号 -> 临时覆盖配置 -> 调用内层任务 -> 读取下一次调度 -> 恢复配置 -> 更新账号状态”这条链路依次执行。
        # 这样可以保证前一个账号的临时改动不会污染下一个账号，外层调度也能在每个账号执行后准确地更新下一次该执行的时间。
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
                # 先把内层任务执行后可能变更的挂卡状态同步到当前账号对象，
                # 再恢复全局配置。因为恢复配置会把内层任务的 card_type / card_not_found_count 覆盖回原始值，
                # 如果不先同步，当前账号的状态就会被丢掉。
                self._sync_account_activation_state(account_info)

                # 再读取内层任务执行完后的 next_run，并恢复临时配置。
                # 这样不会因为恢复配置而丢失原任务的下一次调度信息。
                next_activation_time = self._get_kekkai_activation_next_run_time()
                self._restore_activation_config()

            # 读取结界挂卡任务执行完后的下一次调度时间，并记录给当前账号
            if next_activation_time:
                self.fade_conf.update_account_next_activation_time(account_info, next_activation_time)
            else:
                # 如果原任务没有给出明确下一次时间，就给一个默认的1小时后重试时间
                self.fade_conf.update_account_next_activation_time(account_info, datetime.now() + timedelta(hours=1))
                logger.warning('%s-%s no next activation time returned; keep current schedule %s', account_info.character, account_info.svr, account_info.next_activation_time)


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
        把当前账号的挂卡配置临时写入内层 KekkaiActivation 任务。

        这一步的作用不是修改配置文件中的永久值，而是把当前账号的私有策略临时覆盖到全局的内层任务配置上，
        让内层任务在本次执行中使用当前账号的挂卡设置。其核心思路如下：
        1. 先把当前内层任务的配置和 scheduler 状态完整备份下来；
        2. 再把当前账号的“账号独占字段”写入内层任务配置；
        3. 把公共字段从外层多账号任务的公共配置里取出来，避免把所有账号都绑到同一份配置上；
        4. 执行完内层任务后，在 finally 中把这些临时修改恢复回去，确保下一个账号不会继承前一个账号的状态。

        这样做的意义是：外层任务负责“调度”，内层任务负责“真实执行”，两者之间通过临时配置切换来解耦，
        从而在多账号场景下始终保持各账号独立、互不污染。
        """
        activation_config = self.config.kekkai_activation.activation_config
        public_config = self.fade_conf.multi_account_kekkai_activation_config

        # 先保存一份原始配置，后面执行完后恢复，避免影响下一个账号
        self._activation_config_backup = {
            'card_type': activation_config.card_type,
            'card_star': activation_config.card_star,
            'swipe_retry_limit': activation_config.swipe_retry_limit,
            'min_taiko_num': activation_config.min_taiko_num,
            'min_fish_num': activation_config.min_fish_num,
            'exchange_before': activation_config.exchange_before,
            'exchange_max': activation_config.exchange_max,
            'auto_fill': activation_config.auto_fill,
            'shikigami_class': activation_config.shikigami_class,
            'card_not_found_count': activation_config.card_not_found_count,
        }

        # 同时备份内层任务的 scheduler，避免内层任务执行时修改 next_run 后污染下一个账号。
        self._kekkai_activation_scheduler_backup = copy.deepcopy(self.config.kekkai_activation.scheduler)

        # 账号独立字段使用当前账号自己的配置，公共行为字段使用多账号任务的公共配置
        activation_config.card_type = account_info.card_type
        activation_config.card_star = account_info.card_star
        activation_config.swipe_retry_limit = account_info.swipe_retry_limit
        activation_config.min_taiko_num = account_info.min_taiko_num
        activation_config.min_fish_num = account_info.min_fish_num
        activation_config.exchange_before = public_config.exchange_before
        activation_config.exchange_max = public_config.exchange_max
        activation_config.auto_fill = account_info.auto_fill
        activation_config.shikigami_class = account_info.shikigami_class
        activation_config.card_not_found_count = account_info.card_not_found_count
        self.config.save()

    def _sync_account_activation_state(self, account_info):
        """
        把内层 KekkaiActivation 任务执行后的挂卡状态同步回当前账号对象。

        这里要同步的是：
        - card_type：内层任务在未找到卡时可能会切换卡类型；
        - card_not_found_count：内层任务在未找到卡时会增加计数器；

        这两个值都来自内层任务本轮真实执行后的结果，而不是外层恢复配置前的初始值。
        只有在这里把它们写回当前账号对象后，下一次同一个账号进入多账号轮询时，才不会把“上一次已经变更过的状态”丢掉。
        """
        activation_config = self.config.kekkai_activation.activation_config
        account_info.card_type = activation_config.card_type
        account_info.card_not_found_count = activation_config.card_not_found_count

    def _restore_activation_config(self):
        """
        恢复内层 KekkaiActivation 任务的原始配置与调度状态。

        每个账号执行完一次挂卡后，都需要把之前临时写入的配置和 scheduler 状态恢复回去。
        这是整个多账号挂卡逻辑能稳定运行的关键：如果只恢复一部分内容，后一个账号可能仍然拿到前一个账号临时覆盖后的值，
        甚至会继承被内层任务改写后的 next_run，导致后续调度错乱。
        """
        if not hasattr(self, '_activation_config_backup'):
            return
        activation_config = self.config.kekkai_activation.activation_config
        activation_config.card_type = self._activation_config_backup['card_type']
        activation_config.card_star = self._activation_config_backup['card_star']
        activation_config.swipe_retry_limit = self._activation_config_backup['swipe_retry_limit']
        activation_config.min_taiko_num = self._activation_config_backup['min_taiko_num']
        activation_config.min_fish_num = self._activation_config_backup['min_fish_num']
        activation_config.exchange_before = self._activation_config_backup['exchange_before']
        activation_config.exchange_max = self._activation_config_backup['exchange_max']
        activation_config.auto_fill = self._activation_config_backup['auto_fill']
        activation_config.shikigami_class = self._activation_config_backup['shikigami_class']
        activation_config.card_not_found_count = self._activation_config_backup['card_not_found_count']

        if hasattr(self, '_kekkai_activation_scheduler_backup'):
            self.config.kekkai_activation.scheduler = self._kekkai_activation_scheduler_backup

        self.config.save()

    def _get_next_run_time(self) -> datetime:
        """
        计算外层多账号挂卡任务的下一次调度时间。

        规则是：从所有有效账号里取出每个账号当前维护的 next_activation_time，找出其中最早的那一个。
        这样可以确保只要任意一个账号已经达到挂卡时机，外层任务就会在最早需要执行的那一个时间点重新被唤醒。
        如果当前没有任何有效账号记录到下一次挂卡时间，就回退到一个默认的 1 小时后，避免外层任务没有可执行时机而失去调度。
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
        从内层 KekkaiActivation 任务读取它执行完后的下一次调度时间。

        这里不直接改动内层任务逻辑，而是把它在本轮执行结束后由 scheduler 写入的 next_run 读出来，
        作为当前账号的下次挂卡时间。这个值通常比手工生成一个固定间隔更准确，因为它来自内层任务真实执行后的调度结果。
        如果内层任务没有给出有效的 next_run，就返回 None，交由外层逻辑选择回退策略。
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
