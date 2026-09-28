"""Shared summary/detail read protocol; mode-specific serialization stays in adapters."""
from typing import Awaitable, Callable

from fastapi import APIRouter, HTTPException

from module.server.multi_account_feature_registry import MultiAccountFeature


Account = dict
ListAccounts = Callable[[str, int | None], Awaitable[dict]]


def task_counts(tasks: list[dict]) -> dict:
    return {
        "enabled_task_count": len(tasks),
        "completed_task_count": sum(task["status"] == "completed" for task in tasks),
        "failed_task_count": sum(task["status"] in {"failed", "unfinished"} for task in tasks),
    }


def summarize_task_account(account: Account) -> Account:
    tasks = [task for task in account["tasks"] if task["enabled"]]
    return {key: value for key, value in account.items() if key != "tasks"} | task_counts(tasks)


def summarize_group_account(account: Account) -> Account:
    tasks = []
    groups = []
    for batch in account["fixed_time_batches"]:
        enabled = [task for task in batch["tasks"] if task["enable"]]
        if batch["enable"]:
            tasks.extend(enabled)
        groups.append({key: value for key, value in batch.items() if key != "tasks"} | task_counts(enabled))
    result = {key: value for key, value in account.items()
              if key not in {"fixed_time_batches", "single_tasks"}}
    result["fixed_time_batches"] = groups
    if "single_tasks" in account:
        singles = account["single_tasks"]
        tasks.extend(task for single in singles if single["enable"]
                     for task in single["tasks"] if task["enable"])
        result["single_tasks"] = [{key: value for key, value in single.items() if key != "tasks"}
                                  for single in singles]
    return result | task_counts(tasks)


def register_account_read_routes(
    router: APIRouter,
    feature: MultiAccountFeature,
    list_accounts: ListAccounts,
    summarize: Callable[[Account], Account],
    list_summaries: Callable[[str], Awaitable[dict]] | None = None,
):
    """Register stable paths without introducing task-name-specific branches.

    The adapter filters before serializing a single account. The summary
    builder is injectable so new modes can add their own metadata.
    """
    prefix = f"/{{script_name}}/{feature.api_prefix}"

    async def list_task_account_summaries(script_name: str):
        if list_summaries is not None:
            return await list_summaries(script_name)
        data = await list_accounts(script_name, None)
        return {"accounts": [summarize(account) for account in data["accounts"]]}

    async def get_task_account(script_name: str, account_index: int):
        data = await list_accounts(script_name, account_index)
        account = next((item for item in data["accounts"] if item["index"] == account_index), None)
        if account is None:
            raise HTTPException(status_code=404, detail="运行账号不存在")
        return account

    router.add_api_route(prefix + "/account-summaries", list_task_account_summaries, methods=["GET"],
                         name=f"{feature.key}_account_summaries")
    router.add_api_route(prefix + "/accounts/{account_index}", get_task_account, methods=["GET"],
                         name=f"{feature.key}_account_detail")
    return list_task_account_summaries, get_task_account
