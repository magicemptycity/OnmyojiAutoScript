from typing import ClassVar

from tasks.MultiAccountTaskOrchestration.script_task import ScriptTask as MultiAccountRepeatNewBase
from tasks.MultiAccountCooperation.config import MultiAccountCooperation


class ScriptTask(MultiAccountRepeatNewBase):
    """按账号顺序执行协战任务列表。"""

    task_name: ClassVar[str] = "MultiAccountCooperation"
    multi_account_config_attr: ClassVar[str] = "multi_account_cooperation"
    overview_kind: ClassVar[str] = "cooperation"
    fade_conf: MultiAccountCooperation = None
    task_display_names: ClassVar[dict[str, str]] = {
        "MultiAccountCooperation": "多账号协战",
    }

    def _has_orchestration_items(self) -> bool:
        return False
