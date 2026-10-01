"""Sequential named normal instances within one OAS scheduler task."""
import copy
from datetime import datetime, timedelta
from typing import ClassVar

from module.config.utils import convert_to_underscore
from module.exception import TaskEnd
from module.logger import logger
from tasks.MultiAccountRepeatNewNormal.script_task import ScriptTask as NormalScriptTask


class ScriptTask(NormalScriptTask):
    task_name: ClassVar[str] = "MultiAccountRepeatNewCollection"
    multi_account_config_attr: ClassVar[str] = "multi_account_repeat_new_collection"
    overview_kind: ClassVar[str] = "normal_collection"
    task_display_names: ClassVar[dict[str, str]] = {
        "MultiAccountRepeatNewCollection": "多账号多任务新集合",
    }

    def _save_repeat_config(self) -> None:
        # Save the containing section, never overwrite the independent normal section.
        self.config.save_selected_fields({self.multi_account_config_attr: self.collection})

    def _publish_multi_account_overview(self, kind, active) -> None:
        if active is not None and getattr(self, "_active_instance", None) is not None:
            active = {**active, "instance_id": self._active_instance.instance_id}
        super()._publish_multi_account_overview(kind, active)

    def _apply_private_task_config(self, task_name, task_entry):
        if task_entry is None or not self.collection.share_runtime_records:
            return super()._apply_private_task_config(task_name, task_entry)
        account = getattr(self, "current_account_info", None)
        identifier = getattr(account, "public_account_identifier", "").strip()
        shared = self.collection.shared_runtime_records.get(identifier, {}).get(
            convert_to_underscore(task_name), {}
        )
        if not identifier or not shared:
            return super()._apply_private_task_config(task_name, task_entry)
        local = task_entry.runtime_record
        try:
            # Shared values win over older instance-local records. The task's own
            # today_is_done logic still determines which actions are skipped.
            task_entry.runtime_record = self._merge_runtime(local, shared)
            return super()._apply_private_task_config(task_name, task_entry)
        finally:
            task_entry.runtime_record = local

    @staticmethod
    def _merge_runtime(local: dict, shared: dict) -> dict:
        result = copy.deepcopy(local)
        for key, value in shared.items():
            if key == "scheduler":
                continue
            if isinstance(value, dict) and isinstance(result.get(key), dict):
                result[key] = ScriptTask._merge_runtime(result[key], value)
            else:
                result[key] = copy.deepcopy(value)
        return result

    def _persist_partial_runtime_record(self) -> bool:
        # A completed subaction can write its record before a later subaction fails.
        return self.collection.share_runtime_records

    def _save_private_runtime_record(self, task_entry, backup_info) -> None:
        if task_entry is None or backup_info is None:
            return
        previous = copy.deepcopy(task_entry.runtime_record)
        super()._save_private_runtime_record(task_entry, backup_info)
        changes = task_entry.runtime_record
        if not self.collection.share_runtime_records:
            return
        task_entry.runtime_record = self._merge_runtime(previous, changes)
        if not changes:
            return
        account = getattr(self, "current_account_info", None)
        identifier = getattr(account, "public_account_identifier", "").strip()
        if not identifier:
            return
        key = convert_to_underscore(getattr(task_entry, "task_name", ""))
        if not key:
            return
        by_account = self.collection.shared_runtime_records.setdefault(identifier, {})
        by_account[key] = self._merge_runtime(by_account.get(key, {}), changes)
        self._save_repeat_config()

    def run(self):
        self.collection = getattr(self.config, self.multi_account_config_attr)
        self._account_scope = getattr(self, "current_account_info", None)
        if self._delay_for_server_update_before_accounts():
            raise TaskEnd(self.task_name)
        now = datetime.now()
        due = sorted(
            (instance for instance in self.collection.instance_list
             if instance.normal.scheduler.enable and instance.normal.scheduler.next_run <= now),
            key=lambda instance: (
                instance.normal.scheduler.next_run,
                instance.normal.scheduler.priority,
            ),
        )
        for instance in due:
            self._active_instance = instance
            self.fade_conf = instance.normal
            self._rerun_incomplete_accounts_done = False
            logger.hr(f"集合实例：{instance.name}", 2)
            try:
                failed = self._run_normal_accounts()
            finally:
                self._active_instance = None
            instance.normal.scheduler.next_run = self._scheduler_next_run(
                instance.normal.scheduler, success=not failed
            )
            self._save_repeat_config()
        next_times = [instance.normal.scheduler.next_run
                      for instance in self.collection.instance_list
                      if instance.normal.scheduler.enable]
        self.set_next_run(
            self.task_name, success=None, server=False,
            target=min(next_times) if next_times else datetime.now() + timedelta(days=1),
        )
        raise TaskEnd(self.task_name)
