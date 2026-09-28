"""Pure copy edits; routers own validation, atomic section copies and scheduling.

Only configuration is copied. New groups get fresh identity and default progress;
existing groups keep their identity, progress and unrelated tasks.
"""
import copy
from datetime import datetime
from uuid import uuid4

from module.config.utils import convert_to_underscore


def copy_task_configuration(source, target) -> None:
    """Do not copy status, completion/progress times or runtime_record."""
    target.config_mode = source.config_mode
    target.private_config = copy.deepcopy(source.private_config)


def copy_group_configuration(source, target_account) -> None:
    existing = next((group for group in target_account.fixed_time_batch_list
                     if group.name == source.name), None)
    group = source.model_copy(deep=True)
    if existing is None:
        group.batch_id = uuid4().hex
        group.last_complete_time = datetime(2023, 1, 1)
        group.task_progress_time = datetime(2023, 1, 1)
        group.completed_task_list = ""
        group.failed_task_list = ""
        group.unfinished_task_list = ""
        for task in group.task_list:
            task.runtime_record = {}
        target_account.fixed_time_batch_list.append(group)
        return
    existing.scheduler = group.scheduler
    existing.item_type = group.item_type
    by_name = {convert_to_underscore(task.task_name): task for task in existing.task_list}
    copied_tasks = []
    for source_task in group.task_list:
        task = by_name.get(convert_to_underscore(source_task.task_name))
        if task is None:
            task = source_task
            task.runtime_record = {}
        else:
            task.enable = source_task.enable
            copy_task_configuration(source_task, task)
        copied_tasks.append(task)
    keys = {convert_to_underscore(task.task_name) for task in group.task_list}
    existing.task_list = [task for task in existing.task_list
                          if convert_to_underscore(task.task_name) not in keys] + copied_tasks
