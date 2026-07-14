from module.config.utils import convert_to_underscore


TASK_NAME_ALIASES = {
    'MultiAccountKekkaiActivation': ['多账号挂卡', '多账号结界挂卡'],
}


class TaskNameResolver:
    @staticmethod
    def _build_aliases(task_name: str) -> list[str]:
        names = []
        names.append(task_name)
        names.append(convert_to_underscore(task_name))
        names.extend(TASK_NAME_ALIASES.get(task_name, []))
        return [n for n in dict.fromkeys(names) if n]

    @classmethod
    def resolve(cls, text: str) -> str | None:
        if not text:
            return None
        normalized = text.strip().lower()
        if not normalized:
            return None
        for task_name, aliases in TASK_NAME_ALIASES.items():
            all_names = [task_name, convert_to_underscore(task_name)] + aliases
            if normalized in (n.lower() for n in all_names):
                return task_name
        return None
