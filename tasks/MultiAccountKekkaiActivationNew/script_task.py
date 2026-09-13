from typing import ClassVar

from tasks.Component.MultiAccount.account_scheduler_task import AccountSchedulerTaskBase
from tasks.KekkaiActivation.config import ActivationConfig


class ScriptTask(AccountSchedulerTaskBase):
    """使用公共账号库和账号独立调度执行结界挂卡。"""

    task_name: ClassVar[str] = "MultiAccountKekkaiActivationNew"
    multi_account_config_attr: ClassVar[str] = "multi_account_kekkai_activation_new"
    priority_config_attr: ClassVar[str] = "multi_account_kekkai_activation_new_config"
    task_display_names: ClassVar[dict[str, str]] = {
        task_name: "多账号多任务挂卡新",
    }
    inner_task_name: ClassVar[str] = "KekkaiActivation"
    inner_task_config_attr: ClassVar[str] = "kekkai_activation"
    settings_group: ClassVar[str] = "activation_config"
    settings_model: ClassVar[type] = ActivationConfig
    next_run_field: ClassVar[str] = "next_activation_time"
    overview_kind: ClassVar[str] = "activation"
    action_label: ClassVar[str] = "结界挂卡"
    persist_private_config_after_run: ClassVar[bool] = True
