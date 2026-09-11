from typing import ClassVar

from module.logger import logger
from tasks.MultiAccountTaskOrchestration.script_task import ScriptTask as MultiAccountRepeatNewBase
from tasks.MultiAccountRepeatNewFixed.config import MultiAccountRepeatNewFixed


class ScriptTask(MultiAccountRepeatNewBase):
    """仅运行账号固定时间任务的独立多账号任务。"""

    task_name: ClassVar[str] = "MultiAccountRepeatNewFixed"
    multi_account_config_attr: ClassVar[str] = "multi_account_repeat_new_fixed"
    fade_conf: MultiAccountRepeatNewFixed = None
    overview_kind: ClassVar[str] = "fixed"
    task_display_names: ClassVar[dict[str, str]] = {
        "MultiAccountRepeatNewFixed": "多账号多任务新固定时间",
    }

    def run(self):
        logger.hr(self._current_task_display_name(), 1)
        self.fade_conf = getattr(self.config, self.multi_account_config_attr)
        self._account_scope = getattr(self, "current_account_info", None)
        # 固定时间版只调度顺序任务组，复用编排基类统一的原生 Scheduler 队列。
        return self._run_orchestration_items()

    def _enabled_single_tasks(self, account_info):
        # 防御性隔离：即使旧数据误带独立任务，也绝不让它进入固定时间版。
        return []
