import importlib
from pathlib import Path

from module.exception import RequestHumanTakeover, TaskEnd
from module.logger import logger
from tasks.Component.SwitchAccount.switch_account import SwitchAccount
from tasks.GameUi.game_ui import GameUi
from tasks.MultiAccountRepeat.assets import MultiAccountRepeatAssets
from tasks.Component.SwitchAccount.assets import SwitchAccountAssets
from tasks.MultiAccountRepeatDay.config import MultiAccountRepeatDay


class ScriptTask(GameUi, MultiAccountRepeatAssets, SwitchAccountAssets):
    fade_conf: MultiAccountRepeatDay = None

    def run(self):
        self.fade_conf = self.config.multi_account_repeat_day

        for account_info in self.fade_conf.account_list:
            logger.info('start account %s-%s', account_info.character, account_info.svr)

            suc = SwitchAccount(self.config, self.device, account_info).switchAccount()
            if not suc:
                logger.warning('switch to %s-%s failed', account_info.character, account_info.svr)
                continue

            task_names = account_info.repeat_task_names
            if not task_names:
                logger.warning('no repeat tasks configured for %s-%s', account_info.character, account_info.svr)
                continue

            for task_name in task_names:
                logger.info('run repeated task %s for %s-%s', task_name, account_info.character, account_info.svr)
                try:
                    task_obj = self.create_task_object(task_name, config=self.config, device=self.device)
                    task_obj.run()
                except TaskEnd:
                    logger.warning('%s-%s task ended for %s', account_info.character, account_info.svr, task_name)
                except RequestHumanTakeover:
                    raise
                except Exception as e:
                    logger.error('run %s failed for %s-%s: %s', task_name, account_info.character, account_info.svr, e)
                    self.set_next_run('MultiAccountRepeatDay', success=False)
                    break

            self.fade_conf.update_account_login_history(account_info)
            self.config.save()

        self.set_next_run('MultiAccountRepeatDay', success=True)
        raise TaskEnd('MultiAccountRepeatDay')

    def create_task_object(self, task_name: str, **kwargs):
        module_path = str(Path.cwd() / 'tasks' / task_name / 'script_task.py')
        spec = importlib.util.spec_from_file_location('script_task', module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.ScriptTask(**kwargs)
