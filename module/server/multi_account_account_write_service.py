"""Common account-list mutations; callers retain mode-specific save and broadcast hooks."""

from typing import Any, Callable

from module.server.multi_account_task_config_service import task_account


def add_account(section: Any, source: Any, account_factory: Callable[[], Any]) -> bool:
    """Return whether the list changed. Duplicate identifiers are a no-op."""
    if any(item.public_account_identifier == source.identifier for item in section.account_list):
        return False
    account = account_factory()
    account.sync_public_account(source)
    section.account_list = [
        item for item in section.account_list if item.public_account_identifier.strip()
    ]
    section.account_list.append(account)
    return True


def set_account_enabled(section: Any, account_index: int, enable: bool) -> None:
    task_account(section, account_index).enabled = enable


def delete_account(section: Any, account_index: int) -> None:
    section.account_list.remove(task_account(section, account_index))
