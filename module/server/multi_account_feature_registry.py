"""统一的新多账号功能描述。"""
from dataclasses import dataclass


@dataclass(frozen=True)
class MultiAccountFeature:
    key: str
    task_name: str
    display_name: str
    mode: str
    api_prefix: str
    settings_path: str | None = None
    settings_group: str | None = None
    next_run_field: str | None = None
    forbid_periods: bool = False
    task_list: bool = False
    fixed_batches: bool = False
    orchestration: bool = False
    overview_kind: str | None = None


# OASX 根据该清单选择通用页面；旧专属 API 继续保留兼容。
MULTI_ACCOUNT_FEATURES = (
    MultiAccountFeature(
        key="multi_account_repeat_new_normal",
        task_name="MultiAccountRepeatNewNormal",
        display_name="多账号多任务新普通",
        mode="normal",
        task_list=True,
        api_prefix="multi_account_repeat_new_normal",
    ),
    MultiAccountFeature(
        key="multi_account_repeat_timed",
        task_name="MultiAccountRepeatTimed",
        display_name="多账号多任务定时",
        mode="timed",
        task_list=True,
        api_prefix="multi_account_repeat_timed",
    ),
    MultiAccountFeature(
        key="multi_account_repeat_new_fixed",
        task_name="MultiAccountRepeatNewFixed",
        display_name="多账号多任务新固定时间",
        mode="fixed_group",
        task_list=True,
        fixed_batches=True,
        api_prefix="multi_account_repeat_new_fixed",
    ),
    MultiAccountFeature(
        key="multi_account_task_orchestration",
        task_name="MultiAccountTaskOrchestration",
        display_name="多账号任务编排",
        mode="orchestration",
        task_list=True,
        fixed_batches=True,
        orchestration=True,
        api_prefix="multi_account_task_orchestration",
    ),
    MultiAccountFeature(
        key="multi_account_kekkai_utilize_new",
        task_name="MultiAccountKekkaiUtilizeNew",
        display_name="多账号多任务蹭卡新",
        mode="account_scheduler",
        api_prefix="multi_account_kekkai_utilize_new",
        settings_path="utilize-args",
        settings_group="utilize_config",
        next_run_field="next_utilize_time",
        forbid_periods=True,
        overview_kind="utilize",
    ),
    MultiAccountFeature(
        key="multi_account_kekkai_activation_new",
        task_name="MultiAccountKekkaiActivationNew",
        display_name="多账号多任务挂卡新",
        mode="account_scheduler",
        api_prefix="multi_account_kekkai_activation_new",
        settings_path="activation-args",
        settings_group="activation_config",
        next_run_field="next_activation_time",
        forbid_periods=False,
        overview_kind="activation",
    ),
)

FEATURE_BY_KEY = {item.key: item for item in MULTI_ACCOUNT_FEATURES}
FEATURE_BY_TASK_NAME = {item.task_name: item for item in MULTI_ACCOUNT_FEATURES}
