from .config import MultiAccountAreaBoss
from .task_name_resolver import TaskNameResolver

__all__ = ["MultiAccountAreaBoss", "ScriptTask", "TaskNameResolver"]


def __getattr__(name: str):
    if name == "ScriptTask":
        from .script_task import ScriptTask
        return ScriptTask
    raise AttributeError(f"module {__name__} has no attribute {name}")


def __dir__() -> list[str]:
    return __all__
