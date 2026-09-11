# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
import time
from datetime import datetime, timedelta, time as dt_time
from enum import Enum
import random

from tasks.Component.SwitchSoul.switch_soul import SwitchSoul
from tasks.RyouToppa.assets import RyouToppaAssets
from tasks.Component.GeneralBattle.general_battle import (
    BattleSettlementProfile,
    GeneralBattle,
)
from tasks.Component.config_base import ConfigBase, Time
from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.page import page_realm_raid, page_main, page_kekkai_toppa, page_shikigami_records
from tasks.RealmRaid.assets import RealmRaidAssets

from module.logger import logger
from module.exception import TaskEnd
from module.atom.image_grid import ImageGrid
from module.base.utils import point2str
from module.base.timer import Timer
from module.exception import GamePageUnknownError


area_map = (
    {
        "fail_sign": (RyouToppaAssets.I_AREA_1_IS_FAILURE_NEW, RyouToppaAssets.I_AREA_1_IS_FAILURE),
        "rule_click": RyouToppaAssets.C_AREA_1,
        "finished_sign": (RyouToppaAssets.I_AREA_1_FINISHED, RyouToppaAssets.I_AREA_1_FINISHED_NEW)
    },
    {
        "fail_sign": (RyouToppaAssets.I_AREA_2_IS_FAILURE_NEW, RyouToppaAssets.I_AREA_2_IS_FAILURE),
        "rule_click": RyouToppaAssets.C_AREA_2,
        "finished_sign": (RyouToppaAssets.I_AREA_2_FINISHED, RyouToppaAssets.I_AREA_2_FINISHED_NEW)
    },
    {
        "fail_sign": (RyouToppaAssets.I_AREA_3_IS_FAILURE_NEW, RyouToppaAssets.I_AREA_3_IS_FAILURE),
        "rule_click": RyouToppaAssets.C_AREA_3,
        "finished_sign": (RyouToppaAssets.I_AREA_3_FINISHED, RyouToppaAssets.I_AREA_3_FINISHED_NEW)
    },
    {
        "fail_sign": (RyouToppaAssets.I_AREA_4_IS_FAILURE_NEW, RyouToppaAssets.I_AREA_4_IS_FAILURE),
        "rule_click": RyouToppaAssets.C_AREA_4,
        "finished_sign": (RyouToppaAssets.I_AREA_4_FINISHED, RyouToppaAssets.I_AREA_4_FINISHED_NEW)
    },
    {
        "fail_sign": (RyouToppaAssets.I_AREA_5_IS_FAILURE_NEW, RyouToppaAssets.I_AREA_5_IS_FAILURE),
        "rule_click": RyouToppaAssets.C_AREA_5,
        "finished_sign": (RyouToppaAssets.I_AREA_5_FINISHED, RyouToppaAssets.I_AREA_5_FINISHED_NEW)
    },
    {
        "fail_sign": (RyouToppaAssets.I_AREA_6_IS_FAILURE_NEW, RyouToppaAssets.I_AREA_6_IS_FAILURE),
        "rule_click": RyouToppaAssets.C_AREA_6,
        "finished_sign": (RyouToppaAssets.I_AREA_6_FINISHED, RyouToppaAssets.I_AREA_6_FINISHED_NEW)
    },
    {
        "fail_sign": (RyouToppaAssets.I_AREA_7_IS_FAILURE_NEW, RyouToppaAssets.I_AREA_7_IS_FAILURE),
        "rule_click": RyouToppaAssets.C_AREA_7,
        "finished_sign": (RyouToppaAssets.I_AREA_7_FINISHED, RyouToppaAssets.I_AREA_7_FINISHED_NEW)
    },
    {
        "fail_sign": (RyouToppaAssets.I_AREA_8_IS_FAILURE_NEW, RyouToppaAssets.I_AREA_8_IS_FAILURE),
        "rule_click": RyouToppaAssets.C_AREA_8,
        "finished_sign": (RyouToppaAssets.I_AREA_8_FINISHED, RyouToppaAssets.I_AREA_8_FINISHED_NEW)
    }
)

TOPPA_POPUP_CLICK_LIMIT = 2          # 点击结界卡片后，最多尝试 2 次打开挑战浮窗
TOPPA_FIRE_CLICK_LIMIT = 2           # 点击挑战按钮后，最多尝试 2 次进入战斗
TOPPA_POPUP_WAIT_TIMEOUT = 3.0       # 点击卡片后等待浮窗出现的超时时间，秒
TOPPA_BATTLE_WAIT_TIMEOUT = 5.0      # 点击挑战后等待进入战斗的超时时间，秒
TOPPA_FIRE_DELAY_RANGE = (2.0, 5.0)  # 点击挑战按钮前的随机延迟范围，秒
TOPPA_TEMPORARY_ERROR_ROUND_LIMIT = 3


class AreaAttackResult(str, Enum):
    """寮突破单个槽位的检查或进攻结果。"""

    ATTACKABLE = "attackable"
    WIN = "win"
    BATTLE_LOSE = "battle_lose"
    AREA_FAILED = "area_failed"
    AREA_FINISHED = "area_finished"
    TEMPORARY_ERROR = "temporary_error"


def random_delay(min_value: float = 2.0, max_value: float = 10.0, decimal: int = 1):
    """
    生成一个指定范围内的随机等待秒数
    """
    random_float_in_range = random.uniform(min_value, max_value)
    return round(random_float_in_range, decimal)


class ScriptTask(GeneralBattle, GameUi, SwitchSoul, RyouToppaAssets):
    medal_grid: ImageGrid = None
    CLICK_REACTION_DELAY = (0.18, 0.22)               # 元素点击后的通用反应延迟范围
    PREPARE_CLICK_DELAY_RANGE = (2.5, 3.5)            # 进入战斗前的准备等待延迟范围
    SETTLEMENT_CLICK_INTERVAL_RANGE = (0.65, 0.95)    # 结算页点击间隔范围

    def _settlement_click_profile(self) -> BattleSettlementProfile:
        """返回寮突破专用的结算/奖励页安全点击范围。

        寮突破的结算页与奖励页布局不同于通用战斗；每次仅从对应页面
        已标注的独立区域中随机选择一个点击。未声明权重时，各区域等概率。
        """
        return BattleSettlementProfile(
            name="ryou_toppa",
            result_win_areas=(
                self.C_RESULT_WIN_RANDOM_CENTER,
                self.C_RESULT_WIN_RANDOM_RIGHT,
            ),
            reward_areas=(
                self.C_REWARD_RANDOM_LEFT,
                self.C_REWARD_RANDOM_RIGHT,
                self.C_REWARD_RANDOM_DOWN,
                self.C_REWARD_RANDOM_BOTTOM,
                self.C_REWARD_RANDOM_TOP,
            ),
        )

    def run(self):
        """
        执行
        :return:
        """
        ryou_config = self.config.ryou_toppa
        time_limit: Time = ryou_config.raid_config.limit_time
        time_delta = timedelta(hours=time_limit.hour, minutes=time_limit.minute, seconds=time_limit.second)
        self.medal_grid = ImageGrid([RealmRaidAssets.I_MEDAL_5, RealmRaidAssets.I_MEDAL_4, RealmRaidAssets.I_MEDAL_3,
                                     RealmRaidAssets.I_MEDAL_2, RealmRaidAssets.I_MEDAL_1, RealmRaidAssets.I_MEDAL_0])

        if ryou_config.switch_soul_config.enable:
            self.goto_page(page_shikigami_records)
            self.run_switch_soul(ryou_config.switch_soul_config.switch_group_team)

        if ryou_config.switch_soul_config.enable_switch_by_name:
            self.goto_page(page_shikigami_records)
            self.run_switch_soul_by_name(ryou_config.switch_soul_config.group_name, ryou_config.switch_soul_config.team_name)

        self.goto_page(page_kekkai_toppa)
        ryou_toppa_start_flag = True
        ryou_toppa_success_penetration = False
        ryou_toppa_admin_flag = False
        # 点击突破
        while 1:
            self.screenshot()
            if self.appear_then_click(RealmRaidAssets.I_REALM_RAID, interval=1):
                continue
            if self.appear(self.I_REAL_RAID_REFRESH, threshold=0.8):
                if self.appear_then_click(self.I_RYOU_TOPPA, interval=1):
                    continue
            # 攻破阴阳寮，说明寮突已开，则退出
            elif self.appear(self.I_SUCCESS_PENETRATION, threshold=0.8):
                ryou_toppa_start_flag = True
                ryou_toppa_success_penetration = True
                break
            # 出现选择寮突说明寮突未开
            elif self.appear(self.I_SELECT_RYOU_BUTTON, threshold=0.8):
                ryou_toppa_start_flag = False
                ryou_toppa_admin_flag = True
                break
            # 出现晴明说明寮突未开
            elif self.appear(self.I_NO_SELECT_RYOU, threshold=0.8):
                ryou_toppa_start_flag = False
                break
            # 出现寮奖励，说明寮突已开
            elif self.appear(self.I_RYOU_REWARD, threshold=0.8) or self.appear(self.I_RYOU_REWARD_90, threshold=0.8):
                ryou_toppa_start_flag = True
                break

        logger.attr('ryou_toppa_start_flag', ryou_toppa_start_flag)
        logger.attr('ryou_toppa_success_penetration', ryou_toppa_success_penetration)
        # 寮突未开 并且有权限， 开开寮突，没有权限则标记失败
        if not ryou_toppa_start_flag:
            if ryou_config.raid_config.ryou_access and ryou_toppa_admin_flag:
                # 作为寮管理，开启今天的寮突
                logger.info("As the manager of the ryou, try to start ryou toppa.")
                self.start_ryou_toppa()
            else:
                logger.info("The ryou toppa is not open and you are a ryou member.")
                self.set_next_run(task='RyouToppa', finish=True, server=True, success=False)
                raise TaskEnd

        # 100% 攻破, 第二天再执行
        if ryou_toppa_success_penetration:
            logger.info('RyouToppa is 100%')
            self.plan_tomorrow_ryoutoppa()
            raise TaskEnd

        if self.config.ryou_toppa.general_battle_config.lock_team_enable:
            logger.info("Lock team.")
            self.ui_click(self.I_TOPPA_UNLOCK_TEAM, self.I_TOPPA_LOCK_TEAM)
        else:
            logger.info("Unlock team.")
            self.ui_click(self.I_TOPPA_LOCK_TEAM, self.I_TOPPA_UNLOCK_TEAM)

        # ----------------------------------------------------------------------------------------------------------
        # 开始进攻：槽位中的敌人会在胜利后下沉重排，每轮按随机顺序检查当前位置。
        # ----------------------------------------------------------------------------------------------------------
        success = True
        temporary_error_rounds = 0

        while True:
            self.device.stuck_record_add('PREPARE_BEFORE_BATTLE')

            if not self.has_ticket():
                logger.info("We have no chance to attack. Try again after 1 hour.")
                success = False
                break
            if self.current_count >= ryou_config.raid_config.limit_count:
                logger.warning("We have attacked the limit count.")
                break
            if datetime.now() >= self.start_time + time_delta:
                logger.warning("We have attacked the limit time.")
                break

            # 进攻期间也可能由本账号或其他寮成员达到 100%。
            self.screenshot()
            if self.appear(self.I_SUCCESS_PENETRATION, threshold=0.8):
                logger.info('RyouToppa is 100%')
                self.plan_tomorrow_ryoutoppa()
                raise TaskEnd

            area_order = list(range(len(area_map)))
            random.shuffle(area_order)
            temporary_errors = 0
            battle_won = False
            has_finished_area = False

            for area_index in area_order:
                if not self.has_ticket():
                    logger.info("We have no chance to attack. Try again after 1 hour.")
                    success = False
                    break
                if self.current_count >= ryou_config.raid_config.limit_count:
                    logger.warning("We have attacked the limit count.")
                    break
                if datetime.now() >= self.start_time + time_delta:
                    logger.warning("We have attacked the limit time.")
                    break

                result = self.attack_area(area_index)
                if result is AreaAttackResult.WIN:
                    # 胜利后目标下沉、后续目标前移，战斗前的位置顺序已经失效。
                    battle_won = True
                    temporary_error_rounds = 0
                    break
                if result is AreaAttackResult.TEMPORARY_ERROR:
                    temporary_errors += 1
                elif result is AreaAttackResult.AREA_FINISHED:
                    has_finished_area = True

            if not success:
                break
            if self.current_count >= ryou_config.raid_config.limit_count:
                break
            if datetime.now() >= self.start_time + time_delta:
                break
            if battle_won:
                # 返回列表后重新建立并打乱位置池，避免漏掉顶到前面的新目标。
                continue

            # 随机扫描完整轮后，只有明确出现已击破目标才能根据下沉排序
            # 判断前方已经没有可进攻目标。八个位置全部失败并不代表整体完成，
            # 还要刷新列表继续检查后续目标。
            if temporary_errors == 0 and has_finished_area:
                logger.info('No attackable area remains before finished targets.')
                self.plan_tomorrow_ryoutoppa()
                raise TaskEnd
            if temporary_errors == 0:
                logger.info('All visible areas failed; refresh to check remaining targets.')
                temporary_error_rounds = 0
                self.flush_area_cache()
                continue

            # 临时 UI 异常不能作为整体完成依据；刷新后进行下一轮随机重试。
            temporary_error_rounds += 1
            if temporary_error_rounds >= TOPPA_TEMPORARY_ERROR_ROUND_LIMIT:
                logger.warning(
                    'RyouToppa has temporary UI errors for %s rounds, retry later.',
                    temporary_error_rounds,
                )
                success = False
                break
            logger.warning(
                'Found %s temporary area errors, refresh and start a new random round.',
                temporary_errors,
            )
            self.flush_area_cache()

        if success:
            self.set_next_run(task='RyouToppa', finish=True, server=True, success=True)
        else:
            self.set_next_run(task='RyouToppa', finish=True, server=True, success=False)
        self.goto_page(page_main)
        raise TaskEnd

    def plan_tomorrow_ryoutoppa(self):
        # 安排下次寮突破，便于复用
        now = datetime.now()
        # 如果时间在00:00-5:00之间则设定时间为当天的自定义时间
        if now.time() < dt_time(5, 0):  # 不确定 time 的使用范围，重命名 datetime 中的 time
            self.custom_next_run(task='RyouToppa', custom_time=self.config.ryou_toppa.raid_config.next_ryoutoppa_time, time_delta=0)
        # 如果时间在05:00-23:59之间则设定时间为明天的自定义时间
        else:
            self.custom_next_run(task='RyouToppa', custom_time=self.config.ryou_toppa.raid_config.next_ryoutoppa_time, time_delta=1)

    def start_ryou_toppa(self):
        """
        开启寮突破
        :return:
        """
        # 点击寮突
        while 1:
            self.screenshot()
            if self.appear_then_click(self.I_SELECT_RYOU_BUTTON, interval=1):
                break
        logger.info(f'Click {self.I_SELECT_RYOU_BUTTON.name}')

        # 选择第一个寮
        while 1:
            self.screenshot()
            if self.appear_then_click(self.I_GUILD_ORDERS_REWARDS, action=self.C_SELECT_FIRST_RYOU, interval=1):
                break
        logger.info(f'Click {self.C_SELECT_FIRST_RYOU.name}')

        # 点击开始突入
        while 1:
            self.screenshot()
            if self.appear_then_click(self.I_START_TOPPA_BUTTON, interval=1):
                continue
            # 出现寮奖励， 说明寮突已开
            if self.appear(self.I_RYOU_REWARD, threshold=0.8):
                break
        logger.info(f'Click {self.I_START_TOPPA_BUTTON.name}')

    def has_ticket(self) -> bool:
        """
        如果没有票了，那么就返回 False
        :return:
        """
        # 21点后、次日5点前无限进攻机会
        if datetime.now().hour >= 21 or datetime.now().hour <= 5:
            return True
        self.wait_until_appear(self.I_TOPPA_RECORD)
        self.screenshot()
        cu, res, total = self.O_NUMBER.ocr(self.device.image)
        if cu == 0 and cu + res == total:
            logger.warning(f'Execute round failed, no ticket')
            return False
        return True

    def check_area(self, index: int) -> AreaAttackResult:
        """检查当前槽位中的敌人状态。"""
        f1, f2 = area_map[index].get("fail_sign")
        f3, f4 = area_map[index].get("finished_sign")
        self.screenshot()

        if self.appear(f3, threshold=0.8) or self.appear(f4, threshold=0.8):
            logger.info('Area [%s] already finished, skip.' % str(index + 1))
            return AreaAttackResult.AREA_FINISHED
        if self.appear(f1, threshold=0.8) or self.appear(f2, threshold=0.8):
            logger.info('Area [%s] is futile attack, skip.' % str(index + 1))
            return AreaAttackResult.AREA_FAILED
        return AreaAttackResult.ATTACKABLE

    def flush_area_cache(self):
        time.sleep(2)
        duration = 0.352
        count = random.randint(1, 3)
        for i in range(count):
            # 测试过很多次 win32api, win32gui 的 MOUSEEVENTF_WHEEL, WM_MOUSEWHEEL
            # 都出现过很多次离奇的事件，索性放弃了使用以下方法，参数是精心调试的。
            # 每次执行刚好刷新一组（2个），设定随机刷新 1 - 3 次。
            safe_pos_x = random.randint(540, 1000)
            safe_pos_y = random.randint(320, 540)
            p1 = (safe_pos_x, safe_pos_y)
            p2 = (safe_pos_x, safe_pos_y - 101)
            logger.info('Swipe %s -> %s, %s ' % (point2str(*p1), point2str(*p2), duration))
            self.device.swipe(
                p1,
                p2,
                duration=duration,
                control_name='RYOU_TOPPA_REFRESH',
            )
            time.sleep(2)

    def attack_area(self, index: int) -> AreaAttackResult:
        """
        攻击指定位置，返回明确的战斗、区域或临时 UI 状态。
        """
        area_status = self.check_area(index)
        if area_status is not AreaAttackResult.ATTACKABLE:
            return area_status

        # 选择目标前可按配置随机等待 2s - 10s
        if self.config.ryou_toppa.raid_config.random_delay:
            delay = random_delay()
            logger.info(f'寮突破选择目标前随机等待: delay={delay:.1f}s')
            time.sleep(delay)

        rcl = area_map[index].get("rule_click")
        # 塔塔开！
        popup_click_count = 0
        fire_click_count = 0
        popup_wait_timer = None
        battle_wait_timer = None
        fire_delay_timer = None
        fire_delay_ready = False
        self.device.click_record_clear()

        while True:
            self.screenshot()

            if self.is_in_battle(False):
                logger.info("Start attach area [%s]" % str(index + 1))
                battle_result = self.run_general_battle(
                    config=self.config.ryou_toppa.general_battle_config
                )
                return (
                    AreaAttackResult.WIN
                    if battle_result
                    else AreaAttackResult.BATTLE_LOSE
                )

            # 点击挑战按钮后只等待进入战斗，不在过渡期间重新点击结界区域
            if battle_wait_timer is not None:
                if not battle_wait_timer.reached():
                    continue
                battle_wait_timer = None
                if fire_click_count >= TOPPA_FIRE_CLICK_LIMIT:
                    logger.warning('挑战按钮点击次数过多，可能已被击破')
                    return AreaAttackResult.TEMPORARY_ERROR
                if not self.appear(RealmRaidAssets.I_FIRE, threshold=0.8):
                    logger.warning('挑战按钮已消失但未识别到战斗，停止重复点击')
                    return AreaAttackResult.TEMPORARY_ERROR

            # 浮窗已出现时只处理挑战按钮
            if self.appear(RealmRaidAssets.I_FIRE, threshold=0.8):
                popup_wait_timer = None
                if not fire_delay_ready:
                    if fire_delay_timer is None:
                        delay = random_delay(*TOPPA_FIRE_DELAY_RANGE)
                        logger.info(f'寮突破点击进攻前随机等待: delay={delay:.1f}s')
                        fire_delay_timer = Timer(delay).start()
                        continue
                    if not fire_delay_timer.reached():
                        continue
                    fire_delay_timer = None
                    fire_delay_ready = True

                if fire_click_count >= TOPPA_FIRE_CLICK_LIMIT:
                    logger.warning('挑战按钮点击次数过多，可能已被击破')
                    return AreaAttackResult.TEMPORARY_ERROR

                if self.appear_then_click(RealmRaidAssets.I_FIRE, interval=2, threshold=0.8):
                    fire_click_count += 1
                    fire_delay_ready = False
                    battle_wait_timer = Timer(TOPPA_BATTLE_WAIT_TIMEOUT).start()
                continue

            # 等待期间目标若被击破或浮窗关闭，取消本次进攻延迟
            fire_delay_timer = None
            fire_delay_ready = False

            # 每次点击结界后给浮窗留出稳定出现时间
            if popup_wait_timer is not None:
                if not popup_wait_timer.reached():
                    continue
                popup_wait_timer = None

            if popup_click_count >= TOPPA_POPUP_CLICK_LIMIT:
                logger.warning('挑战浮窗打开失败，可能已被击破')
                return AreaAttackResult.TEMPORARY_ERROR

            if self.click(rcl, interval=5):
                popup_click_count += 1
                popup_wait_timer = Timer(TOPPA_POPUP_WAIT_TIMEOUT).start()
                continue


if __name__ == "__main__":
    from module.config.config import Config
    from module.device.device import Device

    config = Config('oas1')
    device = Device(config)
    t = ScriptTask(config, device)
    t.run()
