"""Register account-card copy routes from the feature's declared capability."""
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter

from module.server.multi_account_feature_registry import MultiAccountFeature


def register_account_copy_route(router: APIRouter, feature: MultiAccountFeature,
                                endpoint: Callable, *, kind: str) -> None:
    """Fail at import time if a mode advertises a different copy contract."""
    if feature.account_copy_kind != kind:
        raise ValueError(f"{feature.key} 没有声明 {kind} 账号复制能力")
    path = f"/{{script_name}}/{feature.api_prefix}/accounts/{{account_index}}/{kind}/copy"
    if any(route.path == path and 'POST' in route.methods for route in router.routes):
        raise ValueError(f"重复注册账号复制接口：{path}")
    router.add_api_route(path, endpoint, methods=['POST'], name=f'{feature.key}_account_copy')


def register_normal_account_copy_route(router: APIRouter, feature: MultiAccountFeature,
                                       load: Callable[[str], Any],
                                       save: Callable[[str, Any], None]) -> Callable:
    """Same-mode clones inherit the normal task-copy behavior via adapters."""
    if feature.mode not in {"normal", "task_list"}:
        raise ValueError(f"{feature.key} 不是普通任务列表模式")
    from module.server.multi_account_normal_copy import CopyAccountTasksRequest, copy_account_tasks

    async def copy_account_tasks_to_accounts(script_name: str, account_index: int,
                                             request: CopyAccountTasksRequest):
        candidate, result = copy_account_tasks(load(script_name), account_index, request)
        save(script_name, candidate)
        return result

    register_account_copy_route(router, feature, copy_account_tasks_to_accounts, kind="tasks")
    return copy_account_tasks_to_accounts
