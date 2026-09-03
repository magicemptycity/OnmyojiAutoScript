from tasks.GameUi.assets import GameUiAssets
from tasks.GameUi.page import Page, page_daily, page_main
from tasks.GlobalGame.assets import GlobalGameAssets
from tasks.TalismanPass.assets import TalismanPassAssets

# 花合战成就页面
page_accomplishment = Page(TalismanPassAssets.I_ACCOMPLISHMENTS_2)
page_accomplishment.connect(page_daily, GlobalGameAssets.I_UI_BACK_YELLOW, key="page_accomplishment->page_daily")
page_daily.connect(page_accomplishment, TalismanPassAssets.I_ACCOMPLISHMENTS_1, key="page_daily->page_accomplishment")

# 纳物库页面
page_nawu = Page(GameUiAssets.I_CHECK_NAWU)
page_nawu.connect(page_main, GlobalGameAssets.I_UI_BACK_YELLOW, key="page_nawu->page_main")
page_main.connect(page_nawu, GameUiAssets.I_MAIN_GOTO_NAWU, key="page_main->page_nawu")
