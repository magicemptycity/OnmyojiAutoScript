"""Account-card task copy contract shared by normal and cooperation modes."""
import copy

from fastapi import HTTPException
from pydantic import BaseModel

from module.config.utils import convert_to_underscore
from module.server.multi_account_copy_service import copy_task_configuration
from module.server.multi_account_task_config_service import task_account
from tasks.MultiAccountTaskOrchestration.config import MultiAccountRepeatNewTask


class CopyAccountTasksRequest(BaseModel):
    task_names: list[str]
    target_account_indexes: list[int]


def copy_account_tasks(section, account_index: int, request: CopyAccountTasksRequest):
    """Validate all targets, then edit a detached section; keep existing progress."""
    source = task_account(section, account_index)
    requested = {convert_to_underscore(name.strip()) for name in request.task_names if name.strip()}
    selected = [task for task in source.task_list
                if convert_to_underscore(task.task_name) in requested]
    if not requested or len(selected) != len(requested):
        raise HTTPException(status_code=400, detail="所选任务不属于来源账号")
    targets = list(dict.fromkeys(request.target_account_indexes))
    if account_index in targets:
        raise HTTPException(status_code=400, detail="目标账号不能包含来源账号")
    if not targets:
        raise HTTPException(status_code=400, detail="请至少选择一个目标账号")
    for index in targets:
        task_account(section, index)

    candidate = copy.deepcopy(section)
    copied = {}
    selected_keys = {convert_to_underscore(task.task_name) for task in selected}
    for index in targets:
        account = task_account(candidate, index)
        existing = {convert_to_underscore(task.task_name): task for task in account.task_list}
        selected_tasks = []
        for task in selected:
            key = convert_to_underscore(task.task_name)
            target = existing.get(key) or MultiAccountRepeatNewTask(task_name=task.task_name)
            target.task_name = task.task_name
            target.enable = task.enable
            copy_task_configuration(task, target)
            selected_tasks.append(target)
        account.task_list = [task for task in account.task_list
                             if convert_to_underscore(task.task_name) not in selected_keys] + selected_tasks
        copied[index] = [task.task_name for task in selected]
    return candidate, {"success": True, "copied": copied, "failed": {}}
