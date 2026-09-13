from typing import ClassVar

from tasks.Component.MultiAccount.account_scheduler_task import AccountSchedulerTaskBase
from tasks.KekkaiUtilize.config import UtilizeConfig


class ScriptTask(AccountSchedulerTaskBase):
    """使用公共账号库和账号独立调度执行结界蹭卡。"""

    task_name: ClassVar[str] = "MultiAccountKekkaiUtilizeNew"
    multi_account_config_attr: ClassVar[str] = "multi_account_kekkai_utilize_new"
    priority_config_attr: ClassVar[str] = "multi_account_kekkai_utilize_new_config"
    task_display_names: ClassVar[dict[str, str]] = {
        task_name: "多账号多任务蹭卡新",
    }
    inner_task_name: ClassVar[str] = "KekkaiUtilize"
    inner_task_config_attr: ClassVar[str] = "kekkai_utilize"
    settings_group: ClassVar[str] = "utilize_config"
    settings_model: ClassVar[type] = UtilizeConfig
    next_run_field: ClassVar[str] = "next_utilize_time"
    overview_kind: ClassVar[str] = "utilize"
    action_label: ClassVar[str] = "结界蹭卡"
