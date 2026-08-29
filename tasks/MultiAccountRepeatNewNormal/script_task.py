from typing import ClassVar

from tasks.MultiAccountRepeatNew.script_task import ScriptTask as MultiAccountRepeatNewBase
from tasks.MultiAccountRepeatNewNormal.config import MultiAccountRepeatNewNormal


class ScriptTask(MultiAccountRepeatNewBase):
    """仅运行账号普通任务列表的独立多账号任务。"""

    task_name: ClassVar[str] = "MultiAccountRepeatNewNormal"
    multi_account_config_attr: ClassVar[str] = "multi_account_repeat_new_normal"
    fade_conf: MultiAccountRepeatNewNormal = None
    task_display_names: ClassVar[dict[str, str]] = {
        "MultiAccountRepeatNewNormal": "多账号多任务新普通",
    }

    def _has_fixed_time_batches(self) -> bool:
        # 普通版不进入固定时间批次调度。
        return False
