from typing import ClassVar

from tasks.MultiAccountTaskOrchestration.script_task import ScriptTask as MultiAccountRepeatNewBase
from tasks.MultiAccountRepeatNewNormal.config import MultiAccountRepeatNewNormal


class ScriptTask(MultiAccountRepeatNewBase):
    """仅运行账号普通任务列表的独立多账号任务。"""

    task_name: ClassVar[str] = "MultiAccountRepeatNewNormal"
    multi_account_config_attr: ClassVar[str] = "multi_account_repeat_new_normal"
    fade_conf: MultiAccountRepeatNewNormal = None
    task_display_names: ClassVar[dict[str, str]] = {
        "MultiAccountRepeatNewNormal": "多账号多任务新普通",
    }

    def _has_orchestration_items(self) -> bool:
        # 普通版始终执行“账号 → 顺序任务列表”；即使旧数据残留任务组或
        # 私有 scheduler，也不能误切到编排模式。
        return False
