from __future__ import annotations

from module.config.utils import convert_to_underscore


TASK_NAME_ALIASES = {
    'MultiAccountTaskOrchestration': ['多账号任务编排'],
    'MultiAccountRepeatNew': ['多账号多任务新'],
    'MultiAccountRepeatNewNormal': ['多账号多任务新普通'],
    'MultiAccountRepeatNewFixed': ['多账号多任务新固定时间'],
    'Restart': ['重启'],
    'Orochi': ['八岐大蛇'],
    'Sougenbi': ['业原火'],
    'FallenSun': ['日轮之陨'],
    'EternitySea': ['永生之海'],
    'SixRealms': ['六道之门'],
    'OtherWorldTwilight': ['彼世逢魔'],
    'DailyTrifles': ['每日琐事'],
    'DailyTriflesSpecial': ['每日琐事特殊任务', '每日特殊'],
    'AreaBoss': ['地域鬼王'],
    'GoldYoukai': ['金币妖怪'],
    'ExperienceYoukai': ['经验妖怪'],
    'Nian': ['年兽'],
    'TalismanPass': ['花合战'],
    'DemonEncounter': ['逢魔之时'],
    'Pets': ['小猫咪'],
    'SoulsTidy': ['御魂整理'],
    'Delegation': ['式神委派'],
    'WantedQuests': ['悬赏封印'],
    'Tako': ['石距'],
    'AutoCheckinBigGod': ['大神签到'],
    'BondlingFairyland': ['契灵之境'],
    'EvoZone': ['觉醒副本'],
    'GoryouRealm': ['御灵之境'],
    'Exploration': ['探索'],
    'Hyakkiyakou': ['百鬼夜行'],
    'HeroTest': ['英杰试炼'],
    'FindJade': ['寻找协作任务'],
    'MemoryScrolls': ['绘卷'],
    'MultiAccountDelegation': ['多账号委派', '多账号式神委派', '委派任务'],
    'MultiAccountKekkaiActivation': ['多账号挂卡', '多账号结界挂卡'],
    'MultiAccountKekkaiUtilize': ['多账号蹭卡', '多账号结界蹭卡'],
    'MultiAccountAreaBoss': ['多账号地域鬼王', '多账号鬼王'],
    'MultiAccountHunt': ['多账号狩猎战', '多账号狩猎'],
    'KekkaiUtilize': ['结界蹭卡'],
    'KekkaiActivation': ['结界挂卡'],
    'RealmRaid': ['个人突破'],
    'RyouToppa': ['寮突破'],
    'Dokan': ['道馆'],
    'CollectiveMissions': ['集体任务'],
    'Hunt': ['狩猎战'],
    'AbyssShadows': ['狭间暗域'],
    'GuildBanquet': ['寮宴会'],
    'DemonRetreat': ['首领退治'],
    'GuildActivityMonitor': ['寮活动监控'],
    'TrueOrochi': ['真八岐大蛇', '真蛇'],
    'RichMan': ['大富翁'],
    'Secret': ['秘闻副本'],
    'WeeklyTrifles': ['每周琐事'],
    'MysteryShop': ['神秘商店'],
    'Duel': ['斗技'],
    'ActivityShikigami': ['当期爬塔', '爬塔'],
    'MetaDemon': ['超鬼王'],
    'FrogBoss': ['对弈竞猜', '竞猜'],
    'FloatParade': ['花车巡游', '花车'],
    'Quiz': ['智力竞赛', '答题'],
    'KittyShop': ['猫咪铺子', '猫咪商店'],
    'DyeTrials': ['灵染试炼', '灵染'],
    'GuguArtStudio': ['呱呱画室', '画室'],
    'SameHeartTeam': ['同心队'],
    'SameHeartTeamOrochi': ['同心队御魂', '御魂同心队'],
    'SameHeartTeamAwaken': ['同心队觉醒', '觉醒同心队'],
}


class TaskNameResolver:
    @staticmethod
    def _build_aliases(task_name: str) -> list[str]:
        names = []
        names.append(task_name)
        names.append(convert_to_underscore(task_name))
        names.extend(TASK_NAME_ALIASES.get(task_name, []))
        return [n for n in dict.fromkeys(names) if n]

    @classmethod
    def resolve(cls, text: str) -> str | None:
        if not text:
            return None
        normalized = text.strip().lower()
        if not normalized:
            return None

        # 先尝试精确匹配（保持原有逻辑）
        for task_name, aliases in TASK_NAME_ALIASES.items():
            all_names = [task_name, convert_to_underscore(task_name)] + aliases
            if normalized in (n.lower() for n in all_names):
                return task_name

        # 模糊匹配：包含关系（双向）
        for task_name, aliases in TASK_NAME_ALIASES.items():
            all_names = [task_name, convert_to_underscore(task_name)] + aliases
            for name in all_names:
                if normalized in name.lower() or name.lower() in normalized:
                    return task_name

        return None
