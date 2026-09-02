"""多账号任务的高优先级任务让出机制。"""

from module.exception import TaskEnd
from module.logger import logger


class MultiAccountPriorityMixin:
    """为多账号任务提供可选的高优先级任务检查。"""

    priority_config_attr = ""

    def _yield_to_higher_priority_task(self) -> None:
        """在账号切换边界让出已到期的更高优先级任务。"""
        # 多账号多任务已经在外层负责账号和调度，让内层不要重复让出。
        if getattr(self, "_account_scope", None) is not None:
            return

        priority_config = getattr(
            self.fade_conf,
            self.priority_config_attr,
            self.fade_conf,
        )
        if not getattr(priority_config, "check_higher_priority_task", False):
            return

        self.config.update_scheduler()
        current_command = getattr(self, "task_name", "")
        current_priority = getattr(
            getattr(self.fade_conf, "scheduler", None),
            "priority",
            5,
        )
        for task in self.config.pending_task:
            if task.command == current_command:
                current_priority = task.priority
                break

        higher_priority_tasks = [
            task
            for task in self.config.pending_task
            if task.command != current_command and task.priority < current_priority
        ]
        if not higher_priority_tasks:
            return

        task_names = ", ".join(task.command for task in higher_priority_tasks)
        logger.info(
            "发现更高优先级待执行任务: %s，结束当前多账号任务并让出调度",
            task_names,
        )
        # 不修改当前任务原有的 next_run，避免它在高优先级任务完成后被重新排到队尾。
        # 当前任务仍保持到期状态；高优先级任务完成后，调度器会继续选择它。
        raise TaskEnd(self.task_name)
