import importlib
from datetime import datetime
from pathlib import Path

from module.exception import RequestHumanTakeover, TaskEnd
from module.logger import logger
from tasks.Component.SwitchAccount.switch_account import SwitchAccount
from tasks.GameUi.game_ui import GameUi
from tasks.MultiAccountAreaBoss.config import MultiAccountAreaBoss, MultiAccountAreaBossAccount
from tasks.MultiAccountAreaBoss.assets import MultiAccountAreaBossAssets
from tasks.Component.SwitchAccount.assets import SwitchAccountAssets


class ScriptTask(GameUi, MultiAccountAreaBossAssets, SwitchAccountAssets):
    fade_conf: MultiAccountAreaBoss = None

    def run(self):
        self.fade_conf = self.config.multi_account_area_boss

        overall_failed = False

        for account_info in self.fade_conf.account_list:
            logger.info('start account %s-%s', account_info.character, account_info.svr)

            suc = SwitchAccount(self.config, self.device, account_info).switchAccount()
            if not suc:
                logger.warning('switch to %s-%s failed', account_info.character, account_info.svr)
                continue

            try:
                task_obj = self.create_area_boss_task(account_info)
                task_obj.run()
            except TaskEnd:
                logger.info('area boss task ended for %s-%s', account_info.character, account_info.svr)
            except RequestHumanTakeover:
                raise
            except Exception as e:
                logger.error('run MultiAccountAreaBoss failed for %s-%s: %s', account_info.character, account_info.svr, e)
                overall_failed = True
                continue

            self.fade_conf.update_account_login_history(account_info)
            self.config.model.multi_account_area_boss = self.fade_conf
            self.config.save()

        self.set_next_run('MultiAccountAreaBoss', success=not overall_failed)
        raise TaskEnd('MultiAccountAreaBoss')

    def create_area_boss_task(self, account_info: MultiAccountAreaBossAccount, **kwargs):
        module_path = str(Path.cwd() / 'tasks' / 'MultiAccountAreaBoss' / 'area_script_task.py')
        spec = importlib.util.spec_from_file_location('area_script_task', module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        task_obj = module.ScriptTask(config=self.config, device=self.device)
        task_obj.current_account_info = account_info
        return task_obj
