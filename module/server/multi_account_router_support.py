from typing import Any

from module.config.utils import convert_to_underscore
from module.server.main_manager import mm
from module.server.multi_account_feature_registry import FEATURE_BY_TASK_NAME


def shared_account_enabled(script_name: str, account: Any) -> bool:
    library = getattr(mm.config_cache(script_name).model, "multi_account_shared_accounts", None)
    if library is None:
        return False
    source = library.find(str(getattr(account, "public_account_identifier", "")))
    return bool(source is not None and source.enabled)


def runnable_account(script_name: str, account: Any) -> bool:
    validator = getattr(account, "is_valid", None)
    valid = bool(validator()) if callable(validator) else True
    return valid and bool(getattr(account, "enabled", True)) and shared_account_enabled(script_name, account)


def is_multi_account_task(task_name: str) -> bool:
    key = convert_to_underscore(task_name)
    registered = {convert_to_underscore(item) for item in FEATURE_BY_TASK_NAME}
    return key in registered or key.startswith("multi_account")
