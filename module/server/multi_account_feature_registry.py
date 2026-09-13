"""统一的新多账号功能描述。"""
from dataclasses import dataclass


@dataclass(frozen=True)
class MultiAccountFeature:
    key: str
    display_name: str
    mode: str
    api_prefix: str


# 页面只依赖 mode 和能力清单；各功能原有 API 仍保持兼容。
MULTI_ACCOUNT_FEATURES = (
    MultiAccountFeature(
        "multi_account_repeat_new_normal",
        "多账号多任务新普通",
        "normal",
        "multi_account_repeat_new_normal",
    ),
    MultiAccountFeature(
        "multi_account_repeat_timed",
        "多账号多任务定时",
        "timed",
        "multi_account_repeat_timed",
    ),
    MultiAccountFeature(
        "multi_account_repeat_new_fixed",
        "多账号多任务新固定时间",
        "fixed_group",
        "multi_account_repeat_new_fixed",
    ),
    MultiAccountFeature(
        "multi_account_task_orchestration",
        "多账号任务编排",
        "orchestration",
        "multi_account_task_orchestration",
    ),
    MultiAccountFeature(
        "multi_account_kekkai_utilize_new",
        "多账号多任务蹭卡新",
        "account_scheduler",
        "multi_account_kekkai_utilize_new",
    ),
)

FEATURE_BY_KEY = {item.key: item for item in MULTI_ACCOUNT_FEATURES}
