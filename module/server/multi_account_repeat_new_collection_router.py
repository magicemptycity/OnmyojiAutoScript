"""Named normal instances and their shared normal-mode account protocol."""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from fastapi.routing import APIRoute
from pydantic import BaseModel

from module.config.multi_account_scheduler import quick_schedule_next_run
from module.server.api_logger import ApiLoggingRoute
from module.server.main_manager import mm
from module.server import multi_account_repeat_new_normal_router as normal_routes
from tasks.Component.config_scheduler import Scheduler
from tasks.MultiAccountRepeatNewCollection.service import (
    add_instance, copy_instance, delete_instance, get_instance, rename_instance,
)

multi_account_repeat_new_collection_app = APIRouter(route_class=ApiLoggingRoute)
BASE = "/{script_name}/multi_account_repeat_new_collection"


class InstanceName(BaseModel):
    name: str


class CollectionSettings(BaseModel):
    share_runtime_records: bool


class InstanceSchedule(BaseModel):
    scheduler: Scheduler


def _section(script_name: str):
    section = getattr(mm.config_cache(script_name).model, "multi_account_repeat_new_collection", None)
    if section is None:
        raise HTTPException(status_code=404, detail="当前配置没有多账号多任务新集合")
    return section


def _instance(section, instance_id: str):
    try:
        return get_instance(section, instance_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _save(script_name: str, section) -> None:
    mm.config_cache(script_name).save_selected_fields({"multi_account_repeat_new_collection": section})


async def _broadcast_schedule(script_name: str) -> None:
    process = mm.script_process.get(script_name)
    if process is None:
        return
    config = mm.config_cache(script_name)
    config.get_next()
    await process.broadcast_state({"schedule": config.get_schedule_data()})


def _sync_outer(section) -> None:
    next_runs = [item.normal.scheduler.next_run for item in section.instance_list
                 if item.normal.scheduler.enable]
    section.scheduler.enable = bool(next_runs)
    section.scheduler.next_run = min(next_runs) if next_runs else datetime.now() + timedelta(days=1)


def _summary(item) -> dict:
    accounts = item.normal.account_list
    progress_times = [account.task_progress_time for account in accounts
                      if account.task_progress_time.year > 2023]
    progress_times.extend(account.last_complete_time for account in accounts
                          if account.last_complete_time.year > 2023)
    latest = max(progress_times) if progress_times else None
    latest_failed = any(account.failed_task_names or account.unfinished_task_names
                        for account in accounts if latest is not None
                        and max(account.task_progress_time, account.last_complete_time) == latest)
    return {
        "id": item.instance_id,
        "name": item.name,
        "enabled": item.normal.scheduler.enable,
        "next_run": item.normal.scheduler.next_run.isoformat(sep=" ", timespec="seconds"),
        "priority": item.normal.scheduler.priority,
        "account_count": len(item.normal.account_list),
        "task_count": sum(len(account.task_list) for account in item.normal.account_list),
        "last_run": latest.isoformat(sep=" ", timespec="seconds") if latest else None,
        "last_result": "failed" if latest_failed else "completed" if latest else None,
    }


@multi_account_repeat_new_collection_app.get(BASE + "/instances")
def list_instances(script_name: str):
    section = _section(script_name)
    return {"share_runtime_records": section.share_runtime_records,
            "instances": [_summary(item) for item in section.instance_list]}


@multi_account_repeat_new_collection_app.put(BASE + "/settings")
def set_settings(script_name: str, request: CollectionSettings):
    section = _section(script_name)
    section.share_runtime_records = request.share_runtime_records
    _save(script_name, section)
    return {"share_runtime_records": section.share_runtime_records}


@multi_account_repeat_new_collection_app.post(BASE + "/instances")
def create_instance(script_name: str, request: InstanceName):
    section = _section(script_name)
    try:
        item = add_instance(section, request.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _save(script_name, section)
    return _summary(item)


@multi_account_repeat_new_collection_app.post(BASE + "/instances/{instance_id}/copy")
def duplicate_instance(script_name: str, instance_id: str, request: InstanceName):
    section = _section(script_name)
    _instance(section, instance_id)
    try:
        item = copy_instance(section, instance_id, request.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _save(script_name, section)
    return _summary(item)


@multi_account_repeat_new_collection_app.put(BASE + "/instances/{instance_id}/name")
def set_name(script_name: str, instance_id: str, request: InstanceName):
    section = _section(script_name)
    _instance(section, instance_id)
    try:
        item = rename_instance(section, instance_id, request.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _save(script_name, section)
    return _summary(item)


@multi_account_repeat_new_collection_app.delete(BASE + "/instances/{instance_id}")
async def remove_instance(script_name: str, instance_id: str):
    section = _section(script_name)
    _instance(section, instance_id)
    removed = delete_instance(section, instance_id)
    _sync_outer(section)
    _save(script_name, section)
    await _broadcast_schedule(script_name)
    return _summary(removed)


@multi_account_repeat_new_collection_app.get(BASE + "/instances/{instance_id}/scheduler")
def get_scheduler(script_name: str, instance_id: str):
    item = _instance(_section(script_name), instance_id)
    return {"scheduler": item.normal.scheduler.model_dump(mode="json")}


@multi_account_repeat_new_collection_app.put(BASE + "/instances/{instance_id}/scheduler")
async def set_scheduler(script_name: str, instance_id: str, request: InstanceSchedule):
    section = _section(script_name)
    item = _instance(section, instance_id)
    item.normal.scheduler = request.scheduler
    _sync_outer(section)
    _save(script_name, section)
    await _broadcast_schedule(script_name)
    return _summary(item)


@multi_account_repeat_new_collection_app.put(BASE + "/instances/{instance_id}/enable")
async def set_enabled(script_name: str, instance_id: str, enabled: bool):
    section = _section(script_name)
    item = _instance(section, instance_id)
    item.normal.scheduler.enable = enabled
    _sync_outer(section)
    _save(script_name, section)
    await _broadcast_schedule(script_name)
    return _summary(item)


@multi_account_repeat_new_collection_app.post(BASE + "/instances/{instance_id}/quick-schedule")
async def quick_schedule(script_name: str, instance_id: str, run_now: bool = True):
    section = _section(script_name)
    item = _instance(section, instance_id)
    item.normal.scheduler.next_run = quick_schedule_next_run(item.normal.scheduler, run_now=run_now)
    _sync_outer(section)
    _save(script_name, section)
    await _broadcast_schedule(script_name)
    return _summary(item)


async def _normal_instance_context(script_name: str, instance_id: str):
    # FastAPI's yield dependency restores context even if the endpoint fails.
    _instance(_section(script_name), instance_id)
    token = normal_routes._active_collection_instance.set(instance_id)
    try:
        yield
    finally:
        normal_routes._active_collection_instance.reset(token)


# Register the existing normal protocol under an instance-scoped prefix. The
# normal page, API client, and write validations stay identical for every instance.
for _route in normal_routes.multi_account_repeat_new_normal_app.routes:
    if not isinstance(_route, APIRoute):
        continue
    _path = _route.path.replace(
        "/{script_name}/multi_account_repeat_new_normal",
        BASE + "/instances/{instance_id}/normal",
        1,
    )
    multi_account_repeat_new_collection_app.add_api_route(
        _path, _route.endpoint, methods=list(_route.methods),
        dependencies=[Depends(_normal_instance_context), *_route.dependencies],
        name="collection_" + _route.name,
    )
