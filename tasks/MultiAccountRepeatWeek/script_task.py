from tasks.MultiAccountRepeat.script_task import ScriptTask as MultiAccountRepeatScriptTask


class ScriptTask(MultiAccountRepeatScriptTask):
    """多账号每周任务。"""

    task_name = "MultiAccountRepeatWeek"
    multi_account_config_attr = "multi_account_repeat_week"
