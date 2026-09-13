from module.server.multi_account_feature_registry import MULTI_ACCOUNT_KEKKAI_UTILIZE_NEW
from module.server.multi_account_scheduler_router_factory import (
    AccountSchedulerRouterSpec,
    create_account_scheduler_router,
)
from tasks.KekkaiUtilize.config import UtilizeConfig
from tasks.MultiAccountKekkaiUtilizeNew.config import (
    MultiAccountKekkaiUtilizeNewAccount,
    MultiAccountKekkaiUtilizeNewForbidPeriod,
)

multi_account_kekkai_utilize_new_app = create_account_scheduler_router(
    AccountSchedulerRouterSpec(
        feature=MULTI_ACCOUNT_KEKKAI_UTILIZE_NEW,
        account_model=MultiAccountKekkaiUtilizeNewAccount,
        period_model=MultiAccountKekkaiUtilizeNewForbidPeriod,
        settings_model=UtilizeConfig,
        public_config_attr="multi_account_kekkai_utilize_new_config",
        public_task_attr="kekkai_utilize",
    )
)
