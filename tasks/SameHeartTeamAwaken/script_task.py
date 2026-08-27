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
from tasks.GameUi.page import page_awake_zones, page_main, page_shikigami_records
from tasks.SameHeartTeam.assets import SameHeartTeamAssets
from tasks.SameHeartTeam.script_task import ScriptTask as SameHeartTeamScriptTask
from tasks.SameHeartTeamAwaken.config import SameHeartTeamAwaken, SameHeartTeamAwakenConfig
from tasks.EvoZone.assets import EvoZoneAssets
from tasks.EvoZone.config import KirinType as EvoZoneKirinType


class ScriptTask(GeneralBattle, GeneralInvite, GeneralBuff, GeneralRoom, SwitchSoul, GameUi, SameHeartTeamAssets, EvoZoneAssets):
    """
    同心队觉醒任务
    """

    name = 'SameHeartTeamAwaken'

    def run(self):
        logger.hr('同心队觉醒', 1)
        self._restart_before_next_task = False
        self._task_success = False
        # 从配置读取当前任务的觉醒同心队设置
        config: SameHeartTeamAwaken = self.config.same_heart_team_awaken
        active_config: SameHeartTeamAwakenConfig = config.same_heart_team_awaken_config

        # 初始化当前已完成次数、最大次数、以及任务最长运行时间
        self.current_count = 0
        self.limit_count = active_config.limit_count
        self.limit_time = timedelta(
            hours=active_config.limit_time.hour,
            minutes=active_config.limit_time.minute,
            seconds=active_config.limit_time.second,
        )
        self.start_time = datetime.now()

        logger.info('同心队觉醒任务开始')

        # 如果配置启用了换御魂功能，则进入式神记录页面执行灵魂切换
        if active_config.switch_soul_config.enable:
            self.goto_page(page_shikigami_records)
            # 使用 switch_soul_config 中的最终 switch_group_team 值执行切换
            self.run_switch_soul(active_config.switch_soul_config.switch_group_team)

        # 如果开启了加成功能，进入主页面打开觉醒加成，并关闭加成窗口
        self.goto_page(page_main)
        if active_config.soul_buff_enable:
            self.open_buff()
            self.awake(is_open=True)
            self.close_buff()

        # 进入觉醒副本选择页面，准备选择麒麟和层数
        self.goto_page(page_awake_zones)

        kirin_button = self.I_LIGHTNING_KIRIN
        match active_config.kirin_type:
            case EvoZoneKirinType.FIREKIRIN:
                kirin_button = self.I_FIRE_KIRIN
            case EvoZoneKirinType.WINDKIRIN:
                kirin_button = self.I_WIND_KIRIN
            case EvoZoneKirinType.WATERKIRIN:
                kirin_button = self.I_WATER_KIRIN
            case EvoZoneKirinType.LIGHTNINGKIRIN:
                kirin_button = self.I_LIGHTNING_KIRIN

        # 选择对应的麒麟类型并进入适合创建队伍的入口
        # 这里会在觉醒页面内点击指定麒麟图标，直到可以出现创建队伍入口或判断失败
        if not SameHeartTeamScriptTask._select_kirin(self, kirin_button):
            logger.warning('未能进入觉醒页面')
            self._finish_task_failure()

        # 选择配置里的觉醒层数，进入目标副本层
        # 如果当前页面无法匹配到目标层，则直接结束任务，避免进入错误副本
        if not SameHeartTeamScriptTask._select_layer(self, active_config.layer, self.L_LAYER_LIST):
            logger.warning(f'未找到对应层数: {active_config.layer}')
            self._finish_task_failure()

        # 如果配置要求锁定阵容，则在当前页面执行锁队操作
        # 这样可以防止在后续页面中误点队伍成员或阵容变动
        if active_config.lock_team_enable:
            self.check_lock(active_config.lock_team_enable, self.I_EVOZONE_LOCK, self.I_EVOZONE_UNLOCK)

        logger.info('点击创建队伍按钮')
        # 循环点击创建队伍入口，直到进入队伍页面
        # 该步骤可能需要多次点击确认按钮才能进入创建队伍页面
        while True:
            self.screenshot()
            if self.appear(self.I_CHECK_TEAM) or self.appear(self.I_CHECK_TEAM_NEW):
                break
            if self.appear_then_click(self.I_FORM_TEAM, interval=1):
                continue

        # 进入同心队页面，开始同心队集结流程
        if not SameHeartTeamScriptTask._enter_same_heart_team_page(self):
            logger.warning('无法进入同心队页面')
            self._finish_task_failure()

        # 进入集结页面，选择集结目标副本
        if not SameHeartTeamScriptTask._enter_gather_page(self):
            logger.warning('无法进入集结页面')
            self._finish_task_failure()

        # 自动选择队员，默认2人，最低1人
        if not SameHeartTeamScriptTask._set_team_member_count(self):
            logger.warning('无法设置队员数量')
            self._finish_task_failure()

        # 点击集结按钮，开始申请副本集结
        logger.info('点击集结按钮')
        if not SameHeartTeamScriptTask._confirm_gather(self):
            logger.warning('无法点击副本集结按钮')
            self._finish_task_failure()

        # 等待集结成功并返回组队页面，只有成功进入组队页面才能继续创建房间
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

        # 创建队伍并设置为私密房间，避免被非目标玩家打扰
        self.create_room()
        self.ensure_private()
        self.create_ensure()

        # 进入房间后开始战斗循环，直到次数或时间结束
        success = True
        while True:
            self.screenshot()
            # 检查是否达到次数或时间上限，如果已到达则退出循环
            if self.current_count >= self.limit_count:
                if self.is_in_room():
                    logger.info('同心队次数已达上限')
                    break
            if datetime.now() - self.start_time >= self.limit_time:
                if self.is_in_room():
                    logger.info('同心队时间已达上限')
                    break

            # 当前不在房间中，先判断是否是房间失效
            # 若界面检测到匹配中或探索界面，说明房间可能已失效
            if not self.is_in_room():
                sleep(0.5)
                if self.appear(self.I_MATCHING) or self.appear(self.I_CHECK_EXPLORATION):
                    sleep(0.5)
                    if self.appear(self.I_MATCHING) or self.appear(self.I_CHECK_EXPLORATION):
                        logger.warning('房间已失效，任务失败')
                        success = False
                        break
                continue

            # 仍在房间中，则点击挑战按钮进入战斗
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
                exit_matcher=lambda: (self.appear(self.I_CHECK_TEAM) or self.appear(self.I_CHECK_TEAM_NEW)),
            )

        # 退出当前房间和队伍，保证回到安全状态
        if self.exit_room():
            pass
        if self.exit_team():
            pass

        # 退出同心队模式，解散集结并返回庭院
        if not SameHeartTeamScriptTask._exit_same_heart_team(self):
            # 副本已完成，只标记收尾环境异常；多账号多任务会在下一项前重启游戏。
            logger.warning('未能确认同心队已解散，后续任务前需要重启游戏')
            self._restart_before_next_task = True

        # 关闭之前开启的觉醒加成
        if active_config.soul_buff_enable:
            self.open_buff()
            self.awake(is_open=False)
            self.close_buff()

        self._task_success = success
        if success:
            self.set_next_run(self.name, finish=True, success=True)
        else:
            self.set_next_run(self.name, finish=False, success=False)
        raise TaskEnd(self.name)

    def _finish_task_failure(self) -> None:
        """记录失败并结束任务，避免失败分支被外层任务误判为成功。"""
        self._task_success = False
        self.set_next_run(self.name, finish=False, success=False)
        raise TaskEnd(self.name)


