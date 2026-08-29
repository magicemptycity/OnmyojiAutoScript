from typing import ClassVar

from module.logger import logger
from tasks.MultiAccountRepeatNew.script_task import ScriptTask as MultiAccountRepeatNewBase
from tasks.MultiAccountRepeatNewFixed.config import MultiAccountRepeatNewFixed


class ScriptTask(MultiAccountRepeatNewBase):
    """仅运行账号固定时间任务的独立多账号任务。"""

    task_name: ClassVar[str] = "MultiAccountRepeatNewFixed"
    multi_account_config_attr: ClassVar[str] = "multi_account_repeat_new_fixed"
    fade_conf: MultiAccountRepeatNewFixed = None
    task_display_names: ClassVar[dict[str, str]] = {
        "MultiAccountRepeatNewFixed": "多账号多任务新固定时间",
    }

    def run(self):
        logger.hr(self._current_task_display_name(), 1)
        self.fade_conf = getattr(self.config, self.multi_account_config_attr)
        self._account_scope = getattr(self, "current_account_info", None)
        return self._run_fixed_time_batches()
