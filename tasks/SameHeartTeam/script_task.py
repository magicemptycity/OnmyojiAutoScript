# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
from time import sleep
from datetime import datetime, timedelta

from module.exception import TaskEnd
from module.logger import logger
from tasks.Component.GeneralBattle.general_battle import GeneralBattle
from tasks.Component.GeneralInvite.general_invite import GeneralInvite
from tasks.Component.GeneralBuff.general_buff import GeneralBuff
from tasks.Component.GeneralRoom.general_room import GeneralRoom
from tasks.Component.SwitchSoul.switch_soul import SwitchSoul
from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.page import page_main, page_shikigami_records, page_awake_zones, page_soul_zones
from tasks.SameHeartTeam.assets import SameHeartTeamAssets
from tasks.SameHeartTeam.config import SameHeartTeamMode, SameHeartTeamTeamNum
from tasks.Orochi.assets import OrochiAssets
from tasks.EvoZone.assets import EvoZoneAssets
from tasks.Orochi.config import Layer as OrochiLayer
from tasks.EvoZone.config import KirinType
from tasks.Orochi.page import page_orochi


class ScriptTask(GameUi,  GeneralBattle, GeneralInvite, GeneralBuff, GeneralRoom, SwitchSoul,
                 SameHeartTeamAssets, OrochiAssets, EvoZoneAssets):
    """
    同心队独立任务（仅队长模式，无邀请）。
    支持御魂和觉醒两种副本。
    """

    def run(self):
        con = self.config.same_heart_team

        # 每日完成检查已禁用，确保每次都会执行任务

        # 根据模式选择活跃配置和对应资产
        if con.common_config.same_heart_team_mode == SameHeartTeamMode.AWAKEN:
            active_config = con.awaken_config
            zone_page = page_awake_zones
            layer_list = EvoZoneAssets.L_LAYER_LIST  # 觉醒层数列表
            fire_button = self.I_EVOZONE_FIRE
            lock_icon = self.I_EVOZONE_LOCK
            unlock_icon = self.I_EVOZONE_UNLOCK
            kirin_map = {
                KirinType.FIREKIRIN: self.I_FIRE_KIRIN,
                KirinType.WINDKIRIN: self.I_WIND_KIRIN,
                KirinType.WATERKIRIN: self.I_WATER_KIRIN,
                KirinType.LIGHTNINGKIRIN: self.I_LIGHTNING_KIRIN,
            }
            kirin_button = kirin_map.get(active_config.kirin_type, self.I_LIGHTNING_KIRIN)
        else:
            active_config = con.orochi_config
            zone_page = page_soul_zones
            layer_list = OrochiAssets.L_LAYER_LIST  # 御魂层数列表
            fire_button = self.I_OROCHI_FIRE
            lock_icon = self.I_OROCHI_LOCK
            unlock_icon = self.I_OROCHI_UNLOCK
            kirin_button = None  # 御魂不需要麒麟选择

        # 1. 切换御魂（如果需要）
        if active_config.switch_soul_enable:
            self.goto_page(page_shikigami_records)
            # 注意：这里简化处理，实际可能需要根据 preset_enable 决定调用哪个方法
            self.run_switch_soul(active_config.switch_group_team)

        # 2. 打开加成（如果需要）
        if active_config.soul_buff_enable:
            self.goto_page(page_main)
            self.open_buff()
            if con.common_config.same_heart_team_mode == SameHeartTeamMode.OROCHI:
                self.soul(is_open=True)
            else:
                self.awake(is_open=True)
            self.close_buff()

        # 3. 进入对应副本页面
        self.goto_page(zone_page)

        # 4. 如果是觉醒，先选择麒麟类型
        if con.common_config.same_heart_team_mode == SameHeartTeamMode.AWAKEN:
            self._select_kirin(kirin_button)
        # 如果是御魂，直接进入御魂页面
        if con.common_config.same_heart_team_mode == SameHeartTeamMode.OROCHI:
            self.goto_page(page_orochi)

        # 5. 选择层数
        layer = active_config.layer.value if hasattr(active_config.layer, 'value') else active_config.layer
        if not self._select_layer(layer, layer_list):
            logger.warning(f'未找到对应层数: {layer}，任务终止')
            return

        # 6. 锁定阵容（如果需要）
        self.check_lock(active_config.lock_team_enable, lock_icon, unlock_icon)

        # 7. 点击“创建队伍”按钮，进入组队页面
        logger.info('点击创建队伍按钮')
        while 1:
            self.screenshot()
            if self.appear(self.I_CHECK_TEAM) or self.appear(self.I_CHECK_TEAM_NEW):
                break
            if self.appear_then_click(self.I_FORM_TEAM, interval=1):
                continue

        # 8. 进入同心队页面
        if not self._enter_same_heart_team_page():
            logger.warning('无法进入同心队页面')
            return

        # 9. 进入集结页面
        if not self._enter_gather_page():
            logger.warning('无法进入集结页面')
            return

        # 10. 设置队员数量（1或2）
        self._set_team_member_count(con.common_config.team_num)

        # 11. 点击副本集结按钮
        self._confirm_gather()

        # 12. 等待集结成功，进入组队页面
        if not self._wait_for_gather_success():
            logger.warning('集结失败')
            return

        # 13. 在组队页面点击创建队伍图标，进入房间
        logger.info('点击创建队伍图标')
        while 1:
            self.screenshot()
            if self.appear(self.I_CHECK_TEAM) or self.appear(self.I_CHECK_TEAM_NEW):
                break
            if self.appear_then_click(self.I_FORM_TEAM, interval=1):
                continue
        self.create_room()
        self.ensure_private()
        self.create_ensure()

        # 14. 战斗循环（无邀请，直接挑战）
        self.current_count = 0
        self.limit_count = active_config.limit_count
        self.limit_time = timedelta(
            hours=active_config.limit_time.hour,
            minutes=active_config.limit_time.minute,
            seconds=active_config.limit_time.second
        )
        self.start_time = datetime.now()
        success = True

        while 1:
            self.screenshot()

            # 检查次数/时间上限
            if self.current_count >= self.limit_count:
                if self.is_in_room():
                    logger.info('同心队次数已达上限')
                    break
            if datetime.now() - self.start_time >= self.limit_time:
                if self.is_in_room():
                    logger.info('同心队时间已达上限')
                    break

            # 如果不在房间，可能是房间失效
            if not self.is_in_room():
                sleep(0.5)
                if self.appear(self.I_MATCHING) or self.appear(self.I_CHECK_EXPLORATION):
                    sleep(0.5)
                    if self.appear(self.I_MATCHING) or self.appear(self.I_CHECK_EXPLORATION):
                        logger.warning('房间已失效，任务失败')
                        success = False
                        break
                continue

            # 点击挑战按钮（第一次无需额外操作）
            logger.info('点击挑战按钮')
            self.click_fire()
            while 1:
                self.screenshot()
                if self.click_fire():
                    continue
                # 如果挑战按钮消失，说明已进入战斗
                if not self.click_fire():
                    break

            # 执行通用战斗流程
            self.run_general_battle(
                config=active_config.general_battle_config,
                exit_matcher=lambda: (self.appear(self.I_CHECK_TEAM) or self.appear(self.I_CHECK_TEAM_NEW))
            )

        # 15. 退出房间和队伍
        if self.exit_room():
            pass
        if self.exit_team():
            pass

        # 16. 处理同心队解散
        self._exit_same_heart_team()

        # 17. 关闭加成（如果需要）
        if active_config.soul_buff_enable:
            self.open_buff()
            if con.common_config.same_heart_team_mode == SameHeartTeamMode.OROCHI:
                self.soul(is_open=False)
            else:
                self.awake(is_open=False)
            self.close_buff()

        # 18. 记录完成状态
        if success:
            self.set_next_run('SameHeartTeam', finish=True, success=True)
        else:
            self.set_next_run('SameHeartTeam', finish=False, success=False)
        raise TaskEnd('SameHeartTeam')

    # ---------- 辅助方法 ----------

    def _select_kirin(self, kirin_button):
        """选择麒麟类型"""
        logger.info('选择麒麟')
        while True:
            self.screenshot()
            if self.appear(self.I_FORM_TEAM):
                return True
            if self.appear_then_click(kirin_button, interval=1):
                continue
        return False

    def _select_layer(self, layer: str, layer_list) -> bool:
        """选中指定层数"""
        pos = self.list_find(layer_list, layer)
        if pos:
            self.device.click(x=pos[0], y=pos[1], control_name=f'LAYER_{layer}')
            return True
        return False

    def _enter_same_heart_team_page(self) -> bool:
        """从组队页面进入同心队页面"""
        if self.appear_then_click(self.I_I_SAME_HEART_TEAM_ENTER, interval=1):
            for _ in range(10):
                self.screenshot()
                if self.ocr_appear(self.O_O_SAMEHEARTTEAM):
                    return True
                sleep(0.5)
        return False

    def _enter_gather_page(self) -> bool:
        """在同心队页面点击集结图标，进入集结页面"""
        sleep(2)
        self.screenshot()
        if self.appear_then_click(self.I_I_SAME_HEART_TEAM_GATHER, interval=1):
            for _ in range(10):
                self.screenshot()
                if self.appear(self.I_I_GATHER_SELECTED) or self.appear(self.I_I_GATHER_UNSELECTED):
                    return True
                sleep(0.5)
        return False

    def _set_team_member_count(self, team_num: SameHeartTeamTeamNum):
        target = int(team_num)
        logger.info(f'设置队员数量为 {target}')
        for _ in range(10):
            self.screenshot()
            selected_matches = self.I_I_GATHER_SELECTED.match_all_any(self.device.image)
            unselected_matches = self.I_I_GATHER_UNSELECTED.match_all_any(self.device.image)
            current_selected = len(selected_matches)
            current_unselected = len(unselected_matches)
            logger.info(f'当前选中数量: {current_selected}, 未选中数量: {current_unselected}')

            if current_selected == target:
                logger.info('队员数量已符合要求')
                return True
            elif current_selected < target:
                if unselected_matches:
                    x, y = unselected_matches[0][1], unselected_matches[0][2]
                    self.device.click(x, y)
                    logger.info(f'点击未选中位置 ({x}, {y})')
                else:
                    logger.warning('没有未选中图标可点击')
                    break
            else:
                if selected_matches:
                    x, y = selected_matches[0][1], selected_matches[0][2]
                    self.device.click(x, y)
                    logger.info(f'点击选中位置 ({x}, {y})')
                else:
                    logger.warning('没有选中图标可点击')
                    break
            sleep(0.3)
        logger.warning('未能正确设置队员数量')
        return False
    
    def _confirm_gather(self):
        """点击副本集结按钮（三张图片任意匹配）"""
        logger.info('点击副本集结按钮')
        choose_buttons = [
            self.I_I_SAME_HEART_TEAM_GATHER_CHOOSE1,
            self.I_I_SAME_HEART_TEAM_GATHER_CHOOSE2,
            self.I_I_SAME_HEART_TEAM_GATHER_CHOOSE3,
        ]
        for _ in range(5):  # 最多尝试5次
            self.screenshot()
            for btn in choose_buttons:
                if self.appear_then_click(btn, interval=1):
                    logger.info(f'点击了集结按钮: {btn.name}')
                    sleep(0.5)
                    return True
            sleep(0.3)
        logger.warning('未找到任何副本集结按钮')
        return False
    
    def _wait_for_gather_success(self) -> bool:
        """等待集结成功，出现组队图标"""
        for _ in range(20):
            self.screenshot()
            if self.appear(self.I_I_SAME_HEART_TEAM_UP):
                self.appear_then_click(self.I_I_SAME_HEART_TEAM_UP, interval=1)
                for _ in range(10):
                    self.screenshot()
                    if self.appear(self.I_CHECK_TEAM) or self.appear(self.I_CHECK_TEAM_NEW):
                        return True
                    sleep(0.5)
                return False
            if self.appear(self.I_CHECK_TEAM) or self.appear(self.I_CHECK_TEAM_NEW):
                return True
            sleep(0.5)
        return False

    def _exit_same_heart_team(self):
        """退出同心队，解散集结，返回庭院"""
        logger.info('开始处理解散同心队')
        for attempt in range(3):
            self.screenshot()
            if self.appear(self.I_I_SAME_HEART_TEAM_CLOSE):
                logger.info('发现关闭集结按钮，点击关闭')
                self.appear_then_click(self.I_I_SAME_HEART_TEAM_CLOSE, interval=1)
                sleep(1)

                # 等待确认弹窗并点击确认
                for _ in range(10):
                    self.screenshot()
                    # 点击确认
                    if self.appear(self.I_UI_CONFIRM):
                        logger.info('确认解散')
                        self.appear_then_click(self.I_UI_CONFIRM, interval=1)
                        sleep(0.5)
                        break
                    sleep(0.5)

                # 返回庭院
                #self.goto_page(page_main)
                self.click(self.I_UI_BACK_YELLOW)
                return
            else:
                logger.info('未找到关闭按钮，点击左上角返回')
                self.click(self.I_UI_BACK_YELLOW)
                sleep(1)
        self.goto_page(page_main)
