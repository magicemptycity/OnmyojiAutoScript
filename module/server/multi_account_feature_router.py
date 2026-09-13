"""通用新多账号协议入口。

保留各功能原有路由，本路由只提供通用能力发现和只读摘要，供通用页面逐步迁移。
"""
from fastapi import APIRouter, HTTPException

from module.server.main_manager import mm
from module.server.multi_account_feature_registry import FEATURE_BY_KEY, MULTI_ACCOUNT_FEATURES

multi_account_feature_app = APIRouter()


@multi_account_feature_app.get("/{script_name}/multi-account/features")
async def list_multi_account_features(script_name: str):
    model = mm.config_cache(script_name).model
    return {
        "features": [
            {
                "protocol_version": feature.protocol_version,
                "key": feature.key,
                "task_name": feature.task_name,
                "name": feature.display_name,
                "mode": feature.mode,
                "api_prefix": feature.api_prefix,
                "settings_path": feature.settings_path,
                "settings_group": feature.settings_group,
                "next_run_field": feature.next_run_field,
                "forbid_periods": feature.forbid_periods,
                "task_list": feature.task_list,
                "fixed_batches": feature.fixed_batches,
                "orchestration": feature.orchestration,
                "overview_kind": feature.overview_kind,
                "available": getattr(model, feature.key, None) is not None,
            }
            for feature in MULTI_ACCOUNT_FEATURES
        ]
    }


@multi_account_feature_app.get("/{script_name}/multi-account/{feature_key}/manifest")
async def get_multi_account_feature_manifest(script_name: str, feature_key: str):
    feature = FEATURE_BY_KEY.get(feature_key)
    if feature is None:
        raise HTTPException(status_code=404, detail="未知的新多账号功能")
    section = getattr(mm.config_cache(script_name).model, feature.key, None)
    if section is None:
        raise HTTPException(status_code=404, detail="当前实例没有该功能配置")
    return {
        "protocol_version": feature.protocol_version,
        "key": feature.key,
        "task_name": feature.task_name,
        "name": feature.display_name,
        "mode": feature.mode,
        "api_prefix": feature.api_prefix,
        "settings_path": feature.settings_path,
        "settings_group": feature.settings_group,
        "next_run_field": feature.next_run_field,
        "task_list": feature.task_list,
        "fixed_batches": feature.fixed_batches,
        "orchestration": feature.orchestration,
        "overview_kind": feature.overview_kind,
        "capabilities": {
            "accounts": hasattr(section, "account_list"),
            "tasks": feature.task_list or any(hasattr(account, "task_list") for account in getattr(section, "account_list", [])),
            "account_scheduler": feature.mode == "account_scheduler",
            "task_scheduler": feature.mode in {"timed", "fixed_group", "orchestration"},
            "config_source": True,
            "forbid_periods": feature.forbid_periods,
        },
    }


@multi_account_feature_app.get("/{script_name}/multi-account/{feature_key}/accounts")
async def list_multi_account_feature_accounts(script_name: str, feature_key: str):
    """返回通用页面需要的账号摘要；详细编辑仍由兼容专属接口处理。"""
    feature = FEATURE_BY_KEY.get(feature_key)
    if feature is None:
        raise HTTPException(status_code=404, detail="未知的新多账号功能")
    section = getattr(mm.config_cache(script_name).model, feature.key, None)
    if section is None:
        raise HTTPException(status_code=404, detail="当前实例没有该功能配置")
    accounts = []
    configured_accounts = [
        account
        for account in getattr(section, "account_list", [])
        if str(getattr(account, "public_account_identifier", "") or "").strip()
    ]
    for index, account in enumerate(configured_accounts, start=1):
        scheduler = getattr(account, "scheduler", None)
        tasks = getattr(account, "task_list", [])
        accounts.append({
            "index": index,
            "identifier": account.public_account_identifier,
            "character": getattr(account, "character", ""),
            "server": getattr(account, "svr", ""),
            "enabled": bool(getattr(account, "enabled", True)),
            "next_run": str(getattr(scheduler, "next_run", "")) if scheduler else None,
            "tasks": [
                {
                    "name": getattr(item, "task_name", ""),
                    "enabled": bool(getattr(item, "enable", True)),
                }
                for item in tasks
                if getattr(item, "task_name", "")
            ],
        })
    return {"feature": feature.key, "mode": feature.mode, "accounts": accounts}
