from module.server.multi_account_feature_registry import MULTI_ACCOUNT_KEKKAI_ACTIVATION_NEW
from module.server.multi_account_scheduler_router_factory import (
    AccountSchedulerRouterSpec,
    create_account_scheduler_router,
)
from tasks.KekkaiActivation.config import ActivationConfig
from tasks.MultiAccountKekkaiActivationNew.config import (
    MultiAccountKekkaiActivationNewAccount,
    MultiAccountKekkaiActivationNewForbidPeriod,
)

multi_account_kekkai_activation_new_app = create_account_scheduler_router(
    AccountSchedulerRouterSpec(
        feature=MULTI_ACCOUNT_KEKKAI_ACTIVATION_NEW,
        account_model=MultiAccountKekkaiActivationNewAccount,
        period_model=MultiAccountKekkaiActivationNewForbidPeriod,
        settings_model=ActivationConfig,
        public_config_attr="multi_account_kekkai_activation_new_config",
        public_task_attr="kekkai_activation",
    )
)
