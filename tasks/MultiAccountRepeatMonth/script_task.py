from tasks.MultiAccountRepeat.script_task import ScriptTask as MultiAccountRepeatScriptTask


class ScriptTask(MultiAccountRepeatScriptTask):
    """多账号每月任务。"""

    task_name = "MultiAccountRepeatMonth"
    multi_account_config_attr = "multi_account_repeat_month"
