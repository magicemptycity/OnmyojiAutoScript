from module.config.utils import convert_to_underscore


TASK_NAME_ALIASES = {
    "MultiAccountHunt": ["多账号狩猎战", "多账号狩猎"],
}


class TaskNameResolver:
    @staticmethod
    def _build_aliases(task_name: str) -> list[str]:
        names = [task_name, convert_to_underscore(task_name)]
        names.extend(TASK_NAME_ALIASES.get(task_name, []))
        return [name for name in dict.fromkeys(names) if name]

    @classmethod
    def resolve(cls, text: str) -> str | None:
        if not text:
            return None
        normalized = text.strip().lower()
        for task_name in TASK_NAME_ALIASES:
            aliases = cls._build_aliases(task_name)
            if normalized in (alias.lower() for alias in aliases):
                return task_name
        return None
