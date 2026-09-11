from tasks.MultiAccountRepeat.script_task import ScriptTask as MultiAccountRepeatScriptTask


class ScriptTask(MultiAccountRepeatScriptTask):
    """多账号多任务凌晨。"""

    task_name = "MultiAccountRepeatMidnight"
    multi_account_config_attr = "multi_account_repeat_midnight"
