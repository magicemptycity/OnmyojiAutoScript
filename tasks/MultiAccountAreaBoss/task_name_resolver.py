from module.config.utils import convert_to_underscore


TASK_NAME_ALIASES = {
    'MultiAccountAreaBoss': ['多账号地域鬼王', '多账号鬼王'],
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

        for task_name, aliases in TASK_NAME_ALIASES.items():
            all_names = [task_name, convert_to_underscore(task_name)] + aliases
            for name in all_names:
                if normalized in name.lower() or name.lower() in normalized:
                    return task_name

        return None
