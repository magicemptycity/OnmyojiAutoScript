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
    protocol_version: int = 1

    def __post_init__(self) -> None:
        """按页面模式补齐能力，并在服务启动时拒绝无效注册。"""
        if not self.key or not self.task_name or not self.api_prefix:
            raise ValueError("多账号功能必须声明 key、task_name 和 api_prefix")
        valid_modes = {
            "account_scheduler",
            "normal",
            "task_list",
            "timed",
            "fixed_group",
            "fixed_batches",
            "orchestration",
        }
        if self.mode not in valid_modes:
            raise ValueError(f"不支持的多账号页面模式：{self.mode}")
        inferred_task_list = self.mode in {
            "normal", "task_list", "timed", "fixed_group", "fixed_batches", "orchestration",
        }
        inferred_fixed = self.mode in {"fixed_group", "fixed_batches", "orchestration"}
        object.__setattr__(self, "task_list", self.task_list or inferred_task_list)
        object.__setattr__(self, "fixed_batches", self.fixed_batches or inferred_fixed)
        object.__setattr__(self, "orchestration", self.orchestration or self.mode == "orchestration")
        if self.mode == "account_scheduler" and not (self.settings_path and self.settings_group and self.next_run_field):
            raise ValueError(f"账号调度功能 {self.key} 缺少配置路径、配置组或下次运行字段")


# OASX 根据该清单选择通用页面；旧专属 API 继续保留兼容。
MULTI_ACCOUNT_FEATURES = (
    MultiAccountFeature(
        key="multi_account_repeat_new_normal",
        task_name="MultiAccountRepeatNewNormal",
        display_name="多账号多任务新普通",
        mode="normal",
        api_prefix="multi_account_repeat_new_normal",
        overview_kind="normal",
    ),
    MultiAccountFeature(
        key="multi_account_cooperation",
        task_name="MultiAccountCooperation",
        display_name="多账号协战",
        mode="normal",
        api_prefix="multi_account_cooperation",
        overview_kind="cooperation",
    ),
    MultiAccountFeature(
        key="multi_account_repeat_timed",
        task_name="MultiAccountRepeatTimed",
        display_name="多账号多任务定时",
        mode="timed",
        api_prefix="multi_account_repeat_timed",
    ),
    MultiAccountFeature(
        key="multi_account_repeat_new_fixed",
        task_name="MultiAccountRepeatNewFixed",
        display_name="多账号多任务新固定时间",
        mode="fixed_group",
        api_prefix="multi_account_repeat_new_fixed",
    ),
    MultiAccountFeature(
        key="multi_account_task_orchestration",
        task_name="MultiAccountTaskOrchestration",
        display_name="多账号任务编排",
        mode="orchestration",
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

def _index_features(attribute: str) -> dict[str, MultiAccountFeature]:
    result: dict[str, MultiAccountFeature] = {}
    for feature in MULTI_ACCOUNT_FEATURES:
        value = str(getattr(feature, attribute))
        if value in result:
            raise ValueError(f"多账号功能的 {attribute} 重复：{value}")
        result[value] = feature
    return result


FEATURE_BY_KEY = _index_features("key")
FEATURE_BY_TASK_NAME = _index_features("task_name")

MULTI_ACCOUNT_KEKKAI_UTILIZE_NEW = FEATURE_BY_KEY["multi_account_kekkai_utilize_new"]
MULTI_ACCOUNT_KEKKAI_ACTIVATION_NEW = FEATURE_BY_KEY["multi_account_kekkai_activation_new"]
