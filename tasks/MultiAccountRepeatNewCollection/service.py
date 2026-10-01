"""Edits for named normal instances, independent from their execution engine."""
from datetime import datetime
from uuid import uuid4

from module.config.multi_account_scheduler import quick_schedule_next_run
from tasks.MultiAccountRepeatNewCollection.config import (
    MultiAccountRepeatNewCollection,
    NormalInstance,
)
from tasks.MultiAccountRepeatNewNormal.config import MultiAccountRepeatNewNormal


def get_instance(collection: MultiAccountRepeatNewCollection, instance_id: str) -> NormalInstance:
    for instance in collection.instance_list:
        if instance.instance_id == instance_id:
            return instance
    raise ValueError("找不到集合实例")


def _validate_name(collection: MultiAccountRepeatNewCollection, name: str, *, exclude: str = "") -> str:
    name = name.strip()
    if not name or len(name) > 60:
        raise ValueError("实例名称长度必须为 1–60 个字符")
    if any(item.name.casefold() == name.casefold() and item.instance_id != exclude
           for item in collection.instance_list):
        raise ValueError("集合内实例名称不能重复")
    return name


def add_instance(collection: MultiAccountRepeatNewCollection, name: str) -> NormalInstance:
    instance = NormalInstance(instance_id=uuid4().hex, name=_validate_name(collection, name))
    # The outer task and each instance start disabled. A new instance may not
    # inadvertently run before its accounts and schedule have been reviewed.
    instance.normal.scheduler.enable = False
    collection.instance_list.append(instance)
    return instance


def rename_instance(collection: MultiAccountRepeatNewCollection, instance_id: str, name: str) -> NormalInstance:
    instance = get_instance(collection, instance_id)
    instance.name = _validate_name(collection, name, exclude=instance_id)
    return instance


def copy_instance(collection: MultiAccountRepeatNewCollection, instance_id: str, name: str) -> NormalInstance:
    source = get_instance(collection, instance_id)
    name = _validate_name(collection, name)
    normal = MultiAccountRepeatNewNormal.model_validate(source.normal.model_dump())
    normal.scheduler.enable = False
    normal.scheduler.next_run = quick_schedule_next_run(normal.scheduler, run_now=False)
    for account in normal.account_list:
        account.last_complete_time = datetime(2023, 1, 1)
        account.task_progress_time = datetime(2023, 1, 1)
        account.completed_task_list = ""
        account.failed_task_list = ""
        account.unfinished_task_list = ""
        account.fixed_time_batch_list = []
        account.fixed_time_batch_progress = {}
        for task in account.task_list:
            task.runtime_record = {}
            task.last_complete_time = datetime(2023, 1, 1)
            task.task_progress_time = datetime(2023, 1, 1)
            task.status = "pending"
    instance = NormalInstance(instance_id=uuid4().hex, name=name, normal=normal)
    collection.instance_list.append(instance)
    return instance


def delete_instance(collection: MultiAccountRepeatNewCollection, instance_id: str) -> NormalInstance:
    instance = get_instance(collection, instance_id)
    collection.instance_list.remove(instance)
    return instance
