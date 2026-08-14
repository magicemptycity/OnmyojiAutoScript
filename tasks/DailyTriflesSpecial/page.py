from tasks.DailyTrifles.assets import DailyTriflesAssets
from tasks.SameHeartTeam.assets import SameHeartTeamAssets
from tasks.GameUi.default_pages import page_friends, page_guild, page_team
from tasks.GameUi.page import Page, page_mall
from tasks.GlobalGame.assets import GlobalGameAssets

# 商店礼包屋页面
page_store_gift_room = Page(DailyTriflesAssets.I_GIFT_RECOMMEND)
page_store_gift_room.connect(page_mall, GlobalGameAssets.I_UI_BACK_YELLOW, key="page_store_gift_room->page_mall")
page_mall.connect(page_store_gift_room, DailyTriflesAssets.I_ROOM_GIFT, key="page_mall->page_store_gift_room")
# 好友吉闻页面
page_friends_luck = Page(DailyTriflesAssets.I_LUCK_TITLE, priority=75)
page_friends_luck.connect(page_friends, DailyTriflesAssets.I_CLOSE_LUCK_RED, key="page_friends_luck->page_friends")
page_friends.connect(page_friends_luck, DailyTriflesAssets.O_LUCK_MSG, key="page_friends->page_friends_luck")
page_friends_luck.add_enter_failure_hooks(DailyTriflesAssets.I_FRIENDSHIP_UP)
# 寮祈愿页面
page_guild_wish = Page(DailyTriflesAssets.I_DT_CHECK_GUILD_WISH)
page_guild_wish.connect(page_guild, GlobalGameAssets.I_UI_BACK_YELLOW, key="page_guild_wish->page_guild")
page_guild.connect(page_guild_wish, DailyTriflesAssets.I_GUILD_TO_WISH, key="page_guild->page_guild_wish")
# 同心队页面
page_same_heart_team = Page(SameHeartTeamAssets.O_O_SAMEHEARTTEAM)
page_same_heart_team.connect(page_team, GlobalGameAssets.I_UI_BACK_YELLOW, key="page_same_heart_team->page_team")
page_team.connect(page_same_heart_team, SameHeartTeamAssets.I_I_SAME_HEART_TEAM_ENTER, key="page_team->page_same_heart_team")
# 一键预存页面
page_one_click_pre_deposit = Page(DailyTriflesAssets.I_I_ONE_CLICK_PRE_DEPOSIT)
page_one_click_pre_deposit.connect(page_same_heart_team, GlobalGameAssets.I_UI_BACK_YELLOW, key="page_one_click_pre_deposit->page_same_heart_team")
page_same_heart_team.connect(page_one_click_pre_deposit, DailyTriflesAssets.I_I_PRE_DEPOSIT, key="page_same_heart_team->page_one_click_pre_deposit")
