"""Shared schedule commit sequence; each mode owns next-run calculation."""
from typing import Any, Awaitable, Callable


async def commit_schedule_change(
    script_name: str,
    section: Any,
    *,
    refresh: Callable[[str, Any], None] | None = None,
    save: Callable[[str, Any], None],
    broadcast: Callable[[str], Awaitable[None]],
) -> None:
    """Refresh before saving; wrappers that refresh internally omit refresh."""
    if refresh is not None:
        refresh(script_name, section)
    save(script_name, section)
    await broadcast(script_name)


async def broadcast_schedule_overviews(script_name: str, kinds: tuple[str, ...], manager: Any) -> None:
    """Notify affected panels, then publish one refreshed OAS schedule."""
    process = manager.script_process.get(script_name)
    if process is None:
        return
    for kind in dict.fromkeys(kinds):
        await process.broadcast_state({"multi_account_overview": {"kind": kind}})
    config = manager.config_cache(script_name)
    config.get_next()
    await process.broadcast_state({"schedule": config.get_schedule_data()})


async def broadcast_schedule_overview(script_name: str, kind: str, manager: Any) -> None:
    await broadcast_schedule_overviews(script_name, (kind,), manager)
