import random
from time import sleep
from datetime import datetime, timedelta

from module.exception import TaskEnd
from module.logger import logger
from tasks.Component.GeneralBattle.general_battle import GeneralBattle
from tasks.Component.GeneralBuff.general_buff import GeneralBuff
from tasks.Component.GeneralInvite.general_invite import GeneralInvite
from tasks.Component.GeneralRoom.general_room import GeneralRoom
from tasks.Component.SwitchSoul.switch_soul import SwitchSoul
from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.page import page_main, page_shikigami_records, page_soul_zones
from tasks.SameHeartTeam.assets import SameHeartTeamAssets
from tasks.SameHeartTeam.script_task import ScriptTask as SameHeartTeamScriptTask
from tasks.SameHeartTeamOrochi.config import SameHeartTeamOrochi, SameHeartTeamOrochiConfig
from tasks.Orochi.assets import OrochiAssets
from tasks.Orochi.page import page_orochi


class ScriptTask(GeneralBattle, GeneralInvite, GeneralBuff, GeneralRoom, SwitchSoul, GameUi, SameHeartTeamAssets, OrochiAssets):
    """
    同心队御魂任务
    """

    task_name = 'SameHeartTeamOrochi'

    def _finish_task_failure(self) -> None:
        """记录失败并结束任务，避免被外层任务误判为成功。"""
        self._task_success = False
        self.set_next_run(self.task_name, finish=False, success=False)
        raise TaskEnd(self.task_name)

    def run(self):
        logger.hr('同心队御魂', 1)
        self._restart_before_next_task = False
        self._task_success = False
        # 从配置读取当前任务的御魂同心队设置
        config: SameHeartTeamOrochi = self.config.same_heart_team_orochi
        active_config: SameHeartTeamOrochiConfig = config.same_heart_team_orochi_config

        # 初始化当前已完成次数、最大次数及任务最长运行时长
        # current_count: 当前已完成的挑战次数
        # limit_count: 最大允许执行的挑战次数
        # limit_time: 任务总运行时间限制
        self.current_count = 0
        self.limit_count = active_config.limit_count
        self.limit_time = timedelta(
            hours=active_config.limit_time.hour,
            minutes=active_config.limit_time.minute,
            seconds=active_config.limit_time.second,
        )
        self.start_time = datetime.now()

        # 如果需要切换灵魂，先进入式神记录页面执行切换操作
        if active_config.switch_soul_enable:
            self.goto_page(page_shikigami_records)
            # 使用 active_config.switch_soul_config 中最终计算出的 switch_group_team 参数
            self.run_switch_soul(active_config.switch_soul_config.switch_group_team)

        logger.info('同心队御魂任务开始')
        # 如果需要加成，则进入主页面打开御魂加成，再关闭加成窗口
        self.goto_page(page_main)
        if active_config.soul_buff_enable:
            self.open_buff()
            self.soul(is_open=True)
            self.close_buff()

        # 进入御魂副本页面
        self.goto_page(page_soul_zones)
        self.goto_page(page_orochi)

        if not SameHeartTeamScriptTask._select_layer(self, active_config.layer, OrochiAssets.L_LAYER_LIST):
            logger.warning(f'未找到对应层数: {active_config.layer}')
            self._finish_task_failure()

        if active_config.lock_team_enable:
            self.check_lock(active_config.lock_team_enable, self.I_OROCHI_LOCK, self.I_OROCHI_UNLOCK)

        logger.info('点击创建队伍按钮')
        # 点击创建队伍按钮，等待进入队伍页面
        while True:
            self.screenshot()
            if self.appear(self.I_CHECK_TEAM) or self.appear(self.I_CHECK_TEAM_NEW):
                break
            if self.appear_then_click(self.I_FORM_TEAM, interval=1):
                continue

        if not SameHeartTeamScriptTask._enter_same_heart_team_page(self):
            logger.warning('无法进入同心队页面')
            self._finish_task_failure()

        if not SameHeartTeamScriptTask._enter_gather_page(self):
            logger.warning('无法进入集结页面')
            self._finish_task_failure()

        # 自动选择队员，默认2人，最低1人
        if not SameHeartTeamScriptTask._set_team_member_count(self):
            logger.warning('无法设置队员数量')
            self._finish_task_failure()

        logger.info('点击集结按钮')
        if not SameHeartTeamScriptTask._confirm_gather(self):
            logger.warning('无法点击副本集结按钮')
            self._finish_task_failure()

        # 等待集结成功，保证页面跳转回队伍界面之后再进行下一步创建房间
        if not SameHeartTeamScriptTask._wait_for_gather_success(self):
            logger.warning('集结失败')
            self._finish_task_failure()

        logger.info('点击创建队伍图标')
        while True:
            self.screenshot()
            if self.appear(self.I_CHECK_TEAM) or self.appear(self.I_CHECK_TEAM_NEW):
                break
            if self.appear_then_click(self.I_FORM_TEAM, interval=1):
                continue

        # 创建房间并设置为私密，避免被其他玩家打扰
        self.create_room()
        self.ensure_private()
        self.create_ensure()

        # 进入房间后开始连续战斗循环
        # 该循环会持续检查是否达到次数或时间上限，以及房间是否仍然有效
        success = True
        while True:
            self.screenshot()
            
            # 活动位面弹窗出现在猫咪奖励处理之前，先关闭它。
            if self.appear_then_click(self.I_UI_BACK_RED, interval=1):
                continue
            # 检查猫咪奖励
            if self.appear_then_click(self.I_PET_PRESENT, action=self.C_RANDOM_RIGHT, interval=1):
                continue

            # 如果达到次数限制或时间限制，则结束任务
            if self.current_count >= self.limit_count:
                if self.is_in_room():
                    logger.info('同心队次数已达上限')
                    break
            if datetime.now() - self.start_time >= self.limit_time:
                if self.is_in_room():
                    logger.info('同心队时间已达上限')
                    break

            # 如果当前已经不在房间中，则继续等待或判断房间是否失效
            if not self.is_in_room():
                sleep(0.5)
                if self.appear(self.I_MATCHING) or self.appear(self.I_CHECK_EXPLORATION):
                    sleep(0.5)
                    if self.appear(self.I_MATCHING) or self.appear(self.I_CHECK_EXPLORATION):
                        logger.warning('房间已失效，任务失败')
                        success = False
                        break
                continue

            # 如果仍然在房间中，则点击挑战按钮进入战斗
            logger.info('点击挑战按钮')
            self.click_fire()
            while True:
                self.screenshot()
                if self.click_fire():
                    continue
                if not self.click_fire():
                    break

            self.run_general_battle(
                config=active_config.general_battle_config,
                exit_matcher=lambda: (self.appear(self.I_CHECK_TEAM) or self.appear(self.I_CHECK_TEAM_NEW))
            )

        # 退出房间和队伍，保证回到入口状态
        if self.exit_room():
            pass
        if self.exit_team():
            pass

        # 退出同心队模式，解散集结并返回庭院
        if not SameHeartTeamScriptTask._exit_same_heart_team(self):
            # 副本已完成，只标记收尾环境异常；多账号多任务会在下一项前重启游戏。
            logger.warning('未能确认同心队已解散，后续任务前需要重启游戏')
            self._restart_before_next_task = True

        # 关闭之前打开的御魂加成
        if active_config.soul_buff_enable:
            self.open_buff()
            self.soul(is_open=False)
            self.close_buff()

        self._task_success = success
        if success:
            self.set_next_run('SameHeartTeamOrochi', finish=True, success=True)
        else:
            self.set_next_run('SameHeartTeamOrochi', finish=False, success=False)
        raise TaskEnd('SameHeartTeamOrochi')


