from tasks.MultiAccountRepeat.script_task import ScriptTask as MultiAccountRepeatScriptTask


class ScriptTask(MultiAccountRepeatScriptTask):
    """多账号多任务下午。"""

    task_name = "MultiAccountRepeatAfternoon"
    multi_account_config_attr = "multi_account_repeat_afternoon"
