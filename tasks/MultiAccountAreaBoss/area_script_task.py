# 这是地域鬼王的内层执行任务。
# 外层多账号任务会把当前账号的私有配置通过 current_account_config 传进来。

import time

import random
import re
from datetime import datetime, time
from module.atom.click import RuleClick
from tasks.Component.GeneralBattle.general_battle import ExitMatcher, GeneralBattle
from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.page import page_area_boss, page_shikigami_records, page_main
from tasks.Component.SwitchSoul.switch_soul import SwitchSoul
from tasks.MultiAccountAreaBoss.assets import MultiAccountAreaBossAssets
from tasks.AreaBoss.config_boss import AreaBossFloor
from module.logger import logger
from module.exception import TaskEnd
from module.atom.image import RuleImage
from types import SimpleNamespace


class ScriptTask(GeneralBattle, GameUi, SwitchSoul, MultiAccountAreaBossAssets):

    def _exit_matcher(self) -> ExitMatcher:
        return self.I_AB_CLOSE_RED

    def _get_active_area_boss_config(self):
        # 私有配置已经由外层任务按账号下标选好；没有私有配置时使用公共配置。
        private_config = getattr(self, 'current_account_config', None)
        if private_config is not None:
            return private_config
        return self.config.multi_account_area_boss.common_config

    def _get_boss_conf(self):
        # 从活跃配置中读取简单的 boss 运行时参数
        config = self._get_active_area_boss_config()
        c = SimpleNamespace()
        c.boss_number = getattr(config, 'boss_number', 3)
        c.boss_reward = getattr(config, 'boss_reward', False)
        c.reward_floor = getattr(config, 'reward_floor', AreaBossFloor.ONE)
        c.use_collect = getattr(config, 'use_collect', False)
        c.Attack_60 = getattr(config, 'Attack_60', False)
        c.lock_team_enable = getattr(config, 'lock_team_enable', False)
        c.switch_team_enable = getattr(config, 'switch_team_enable', False)
        c.switch_soul_enable = getattr(config, 'switch_soul_enable', False)
        c.preset_public_enable = getattr(config, 'preset_public_enable', '-1,-1')
        return c

    def open_filter(self):
        """打开筛选界面"""
        logger.info("openFilter")
        self.ui_click(self.I_FILTER, self.I_AB_FILTER_OPENED, interval=3)

    def switch_to_collect(self):
        while 1:
            self.screenshot()
            if self.appear(self.I_AB_FILTER_TITLE_COLLECTION):
                break
            if self.appear(self.I_AB_FILTER_OPENED):
                self.click(self.C_AB_COLLECTION_BTN, 1.5)
                continue

    def switch_to_famous(self):
        while 1:
            self.screenshot()
            if self.appear(self.I_AB_FILTER_TITLE_FAMOUS):
                break
            if self.appear(self.I_AB_FILTER_OPENED):
                self.click(self.C_AB_FAMOUS_BTN, 1.5)
                continue

    def switch_to_reward(self):
        self.open_filter()
        while 1:
            self.screenshot()
            if self.appear(self.I_AB_FILTER_TITLE_REWARD):
                break
            if self.appear(self.I_AB_FILTER_OPENED):
                self.click(self.C_AB_REWARD_BTN, 1.5)
                continue

    def run(self) -> bool:
        self.check_can_run()
        active_conf = self._get_active_area_boss_config()
        self.config.area_boss.general_battle.lock_team_enable = getattr(active_conf, 'lock_team_enable', False)
        # 将 active_conf 的 standard 配置映射到 area_boss 内部通用配置对象
        self.config.area_boss.general_battle.preset_enable = getattr(active_conf, 'switch_team_enable', False)
        self.config.area_boss.switch_soul.enable = getattr(active_conf, 'switch_soul_enable', False)
        self.config.area_boss.switch_soul.switch_group_team = getattr(active_conf, 'preset_public_enable', '-1,-1')
        self.config.area_boss.switch_soul.enable_switch_by_name = False
        self.config.area_boss.switch_soul.group_name = ''
        self.config.area_boss.switch_soul.team_name = ''
        con = self._get_boss_conf()

        if self.config.area_boss.switch_soul.enable:
            self.goto_page(page_shikigami_records)
            # 使用 area_boss switch_soul 的 switch_group_team 参数进行御魂切换
            self.run_switch_soul(self.config.area_boss.switch_soul.switch_group_team)

        if self.config.area_boss.switch_soul.enable_switch_by_name:
            self.goto_page(page_shikigami_records)
            self.run_switch_soul_by_name(self.config.area_boss.switch_soul.group_name,
                                         self.config.area_boss.switch_soul.team_name)

        self.goto_page(page_area_boss)

        boss_fought = 0
        if con.boss_reward:
            if self.fight_reward_boss():
                boss_fought += 1

        self.open_filter()
        if con.use_collect:
            self.switch_to_collect()
        else:
            self.switch_to_famous()

        if con.boss_number - boss_fought == 3:
            self.boss_fight(self.I_BATTLE_1)
            self.boss_fight(self.I_BATTLE_2)
            self.boss_fight(self.I_BATTLE_3)
        elif con.boss_number - boss_fought == 2:
            self.boss_fight(self.I_BATTLE_1)
            self.boss_fight(self.I_BATTLE_2)
        elif con.boss_number - boss_fought == 1:
            self.boss_fight(self.I_BATTLE_1)
        self.goto_page(page_main)
        self.set_next_run(task='MultiAccountAreaBoss', success=True, finish=False)
        raise TaskEnd

    def check_can_run(self):
        now = datetime.now().time()
        time_not_passed: bool = time(0, 0, 0) <= now <= time(6, 0, 0)
        if time_not_passed:
            logger.error("It's not time to challenge boss")
            self.goto_page(page_main)
            self.set_next_run(task='MultiAccountAreaBoss', server=False, target=datetime.now().replace(hour=10))
            raise TaskEnd

    def boss(self, battle: RuleImage, collect: bool = False):
        logger.info("Script filter")
        while 1:
            self.screenshot()
            if self.appear(self.I_AB_FILTER_OPENED):
                self.click(self.C_AB_FAMOUS_BTN)
                break
            if self.appear_then_click(self.I_FILTER, interval=3):
                continue

        if collect:
            self.switch_to_collect()
        if not (self.appear(self.I_BATTLE_1) or self.appear(self.I_BATTLE_2) or self.appear(self.I_BATTLE_3)):
            logger.error("There is no boss could be challenged")
            return
        logger.info(f'Script area boss {battle}')
        self.ui_click(battle, self.I_AB_CLOSE_RED)
        logger.info("Script fire ")
        while 1:
            self.screenshot()
            if self.appear_then_click(self.I_FIRE, interval=1):
                continue
            if not self.appear(self.I_AB_CLOSE_RED):
                break
        if not self.run_general_battle(self.config.area_boss.general_battle):
            logger.info("地域鬼王第2只战斗失败")
        logger.info("Script close red")
        self.wait_until_appear(self.I_AB_CLOSE_RED)
        self.ui_click(self.I_AB_CLOSE_RED, self.I_FILTER)

    def boss_fight(self, battle: RuleImage, ultra: bool = False, fileter_open: bool = True) -> bool:
        reward_floor = self._get_boss_conf().reward_floor
        if fileter_open and not self.appear(self.I_AB_FILTER_OPENED):
            self.open_filter()
        if not self.open_boss_detail(battle, 3):
            return False

        if self.is_group_ranked():
            self.ui_click_until_disappear(self.I_AB_CLOSE_RED, interval=3)
            return True

        if ultra:
            if not self.get_difficulty():
                if self.appear(self.I_AB_DIFFICULTY_NORMAL):
                    self.switch_difficulty(True)
                elif self._get_boss_conf().Attack_60:
                    self.switch_to_level_60()
                    if not self.start_fight():
                        logger.warning("you are so weakness!")
                        self.wait_until_appear(self.I_AB_CLOSE_RED)
                        self.ui_click_until_disappear(self.I_AB_CLOSE_RED, interval=3)
                        return False
                    self.switch_difficulty(True)
                else:
                    self.ui_click_until_disappear(self.I_AB_CLOSE_RED, interval=3)
                    return False

            match reward_floor:
                case AreaBossFloor.ONE: self.switch_to_floor_1()
                case AreaBossFloor.TEN: self.switch_to_floor_10()
                case AreaBossFloor.DEFAULT: logger.info("Not change floor")
        result = True
        if not self.start_fight():
            result = False
            logger.warning("Area Boss Fight Failed ")
        self.wait_until_appear(self.I_AB_CLOSE_RED)
        self.ui_click_until_disappear(self.I_AB_CLOSE_RED, interval=1)
        return result

    def start_fight(self) -> bool:
        self.check_can_run()
        while 1:
            self.screenshot()
            if self.appear_then_click(self.I_FIRE, interval=1):
                continue
            if not self.appear(self.I_AB_CLOSE_RED):
                break

        return self.run_general_battle(self.config.area_boss.general_battle)

    def switch_to_level_60(self):
        while 1:
            self.screenshot()
            if self.appear(self.I_AB_LEVEL_60):
                break
            if self.appear(self.I_AB_LEVEL_HANDLE):
                x, y = self.I_AB_LEVEL_HANDLE.front_center()
                self.S_AB_LEVEL_RIGHT.roi_front = (x, y, 10, 10)
                self.swipe(self.S_AB_LEVEL_RIGHT)

    def get_difficulty(self) -> bool:
        self.screenshot()
        return self.appear(self.I_AB_DIFFICULTY_JI)

    def switch_difficulty(self, ultra: bool = True):
        _from = self.I_AB_DIFFICULTY_NORMAL if ultra else self.I_AB_DIFFICULTY_JI
        _to = self.I_AB_DIFFICULTY_JI if ultra else self.I_AB_DIFFICULTY_NORMAL
        while 1:
            self.screenshot()
            if self.appear(_to):
                break
            if self.appear(_from):
                self.click(_from, interval=3)
                continue

    def switch_to_floor_1(self):
        self.ui_click(self.C_AB_JI_FLOOR_SELECTED, self.I_AB_JI_FLOOR_LIST_CHECK, interval=3)
        while 1:
            self.screenshot()
            if self.appear(self.I_AB_JI_FLOOR_ONE):
                self.click(self.I_AB_JI_FLOOR_ONE)
                logger.info("Switch to floor 1")
                break
            self.swipe(self.S_AB_FLOOR_DOWN, interval=1)
            self.wait_until_appear(self.I_AB_JI_FLOOR_ONE, False, 1)

    def switch_to_floor_10(self):
        self.ui_click(self.C_AB_JI_FLOOR_SELECTED, self.I_AB_JI_FLOOR_LIST_CHECK, interval=3)
        while 1:
            self.screenshot()
            if self.appear(self.I_AB_JI_FLOOR_TEN):
                self.click(self.I_AB_JI_FLOOR_TEN)
                logger.info("Switch to floor 10")
                break
            self.wait_until_appear(self.I_AB_JI_FLOOR_TEN, False, 1)

    def fight_reward_boss(self):
        BOSS_REWARD_PHOTO1 = [self.C_AB_BOSS_REWARD_PHOTO_1, self.C_AB_BOSS_REWARD_PHOTO_2, self.C_AB_BOSS_REWARD_PHOTO_3]
        BOSS_REWARD_PHOTO2 = [self.C_AB_BOSS_REWARD_PHOTO_MINUS_2, self.C_AB_BOSS_REWARD_PHOTO_MINUS_1]
        need_open_filter, boss_name, photo = self.get_hot_in_reward()
        if photo is None or boss_name == '声望不够':
            return False
        if not need_open_filter:
            return self.boss_fight(photo, True, fileter_open=False)
        logger.info("Swipe to top")
        for i in range(random.randint(1, 3)):
            self.swipe(self.S_AB_FILTER_DOWN)
        for PHOTO in BOSS_REWARD_PHOTO1:
            self.open_filter()
            name = self.get_bossName(PHOTO)
            if self.check_common_chars(str(name), boss_name):
                return self.boss_fight(PHOTO, True, fileter_open=False)
            self.ui_click_until_disappear(self.I_AB_CLOSE_RED)
        for PHOTO in BOSS_REWARD_PHOTO2:
            self.open_filter()
            self.swipe(self.S_AB_FILTER_UP)
            name = self.get_bossName(PHOTO)
            if self.check_common_chars(str(name), boss_name):
                return self.boss_fight(PHOTO, True, fileter_open=False)
            self.ui_click_until_disappear(self.I_AB_CLOSE_RED)
        self.ui_click_until_disappear(self.I_AB_CLOSE_RED)

    def get_hot_in_reward(self):
        self.switch_to_reward()
        boss_configs = [
            {"photo": self.C_AB_BOSS_REWARD_PHOTO_1, "need_swipe": False},
            {"photo": self.C_AB_BOSS_REWARD_PHOTO_2, "need_swipe": False},
            {"photo": self.C_AB_BOSS_REWARD_PHOTO_3, "need_swipe": False},
            {"photo": self.C_AB_BOSS_REWARD_PHOTO_MINUS_2, "need_swipe": True},
            {"photo": self.C_AB_BOSS_REWARD_PHOTO_MINUS_1, "need_swipe": True},
        ]

        def check_boss(photo: RuleClick):
            self.open_filter()
            num = self.get_num_challenge(photo) or 0
            if not num:
                name = '声望不够'
            else:
                name = self.get_bossName(photo)
                if num >= 20000 and not self.appear(self.I_AB_NUM_CHALLENGE_RAIL):
                    logger.info("The number of challenges is enough")
                    return True, num, name
            self.ui_click_until_disappear(self.I_AB_CLOSE_RED)
            return False, num, name

        mx_challenge_boss_name = None
        mx_challenge_num = 0
        photo = None
        for cfg in boss_configs:
            if cfg["need_swipe"]:
                self.open_filter()
                for _ in range(random.randint(1, 3)):
                    self.swipe(self.S_AB_FILTER_UP)
                self.wait_until_appear(cfg["photo"], wait_time=1)
            ret, challenge_num, boss_name = check_boss(cfg["photo"])
            if ret:
                return False, boss_name, cfg["photo"]
            if challenge_num > mx_challenge_num:
                mx_challenge_num = challenge_num
                mx_challenge_boss_name = boss_name
                photo = cfg['photo']
                logger.attr(mx_challenge_num, f'Select:{boss_name},{photo.name}')
        return True, mx_challenge_boss_name if mx_challenge_boss_name else '声望不够', photo if mx_challenge_boss_name else None

    def get_num_challenge(self, click_area):
        if not self.open_boss_detail(click_area, 3):
            logger.info("%s unavailable", str(click_area))
            return 0
        return self.O_AB_NUM_OF_CHALLENGE.ocr_digit(self.device.image)

    def get_bossName(self, click_area):
        if not self.open_boss_detail(click_area, 3):
            logger.info("%s unavailable", str(click_area))
            return 0
        ocrName = self.O_AB_BOSS_NAME.detect_and_ocr(self.device.image)
        bossName = re.sub(r"[\'\[\]]", "", str([result.ocr_text for result in ocrName]))
        return bossName

    def open_boss_detail(self, battle: RuleImage, try_num: int = 3) -> bool:
        try_num = 3 if try_num <= 0 else try_num

        while try_num > 0:
            self.click(battle, interval=3)
            if self.wait_until_appear(self.I_AB_CLOSE_RED, wait_time=3):
                break
            try_num -= 1
        self.screenshot()
        if self.appear(self.I_AB_CLOSE_RED):
            return True
        return False

    def is_group_ranked(self):
        return not self.appear(self.I_AB_GROUP_RANK_NONE)
