from module.config.utils import convert_to_underscore

# 任务名称解析器用于把用户的任务名称输入（包括英文、下划线和中文别名）
# 统一解析为内部的任务类名称。
TASK_NAME_ALIASES = {
    'MultiAccountKekkaiUtilize': ['多账号蹭卡', '多账号结界蹭卡'],
}


class TaskNameResolver:
    """
    多账号蹭卡任务名称解析器。

    该解析器支持以下格式：
    - 任务类名：MultiAccountKekkaiUtilize
    - 下划线格式：multi_account_kekkai_utilize
    - 中文别名：多账号蹭卡、多账号结界蹭卡
    """

    @staticmethod
    def _build_aliases(task_name: str) -> list[str]:
        names = []
        names.append(task_name)
        names.append(convert_to_underscore(task_name))
        names.extend(TASK_NAME_ALIASES.get(task_name, []))
        return [n for n in dict.fromkeys(names) if n]

    @classmethod
    def resolve(cls, text: str) -> str | None:
        """
        将用户输入的任务名称解析为内部任务名。

        :param text: 用户输入文本
        :return: 解析后的内部任务名，无法解析时返回 None
        """
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
