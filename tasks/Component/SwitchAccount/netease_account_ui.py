from __future__ import annotations

import random
import re
import time
from dataclasses import dataclass

from lxml import etree


class AccountUiUnavailable(RuntimeError):
    """无法通过 UIAutomator 获取网易登录账号控件树。"""


@dataclass(frozen=True)
class AccountUiEntry:
    """网易账号下拉列表中的一个可点击账号项。"""

    account: str
    bounds: tuple[int, int, int, int]


class NeteaseAccountUi:
    """通过 UIAutomator 控件树读取和操作网易已保存账号列表。"""

    ACCOUNT_ITEM_ID = "netease_mpay__login_user_item"
    ACCOUNT_LIST_ID = "netease_mpay__user_list"
    # “添加新账号”只会出现在账号列表底部，可作为可靠的到底标志。
    ADD_ACCOUNT_ID = "netease_mpay__add_user"
    # 账号行约 110px：每次随机滑动 2～2.5 个账号，避免固定轨迹。
    SCROLL_DISTANCE_RANGE = (220, 275)
    SCROLL_DURATION_RANGE = (0.50, 0.70)
    SCROLL_SETTLE_RANGE = (0.50, 0.70)
    ACCOUNT_TEXT_IDS = (
        "netease_mpay__login_username_with_tag",
        "netease_mpay__login_username",
    )

    def __init__(self, device, *, settle_seconds: float = 0.5, max_scrolls: int = 12):
        self.device = device
        self.settle_seconds = settle_seconds
        self.max_scrolls = max_scrolls

    @staticmethod
    def normalize_account(account: str | None) -> str:
        return "".join((account or "").split()).casefold()

    @staticmethod
    def _resource_xpath(resource_id: str) -> str:
        length = len(resource_id)
        return (
            "//*["
            f"substring(@resource-id, string-length(@resource-id) - {length - 1}) = \"{resource_id}\""
            "]"
        )

    @staticmethod
    def _bounds(node) -> tuple[int, int, int, int]:
        values = [int(value) for value in re.findall(r"\d+", node.attrib.get("bounds", ""))]
        if len(values) != 4:
            raise AccountUiUnavailable("账号控件坐标无效")
        left, top, right, bottom = values
        if right <= left or bottom <= top:
            raise AccountUiUnavailable("账号控件坐标为空")
        return left, top, right, bottom

    def dump(self):
        """读取网易登录弹窗的多窗口控件树。"""
        last_error = None
        for attempt in range(2):
            try:
                content = self.device.u2.dump_hierarchy(compressed=False, pretty=False)
                return etree.fromstring(content.encode("utf-8"))
            except Exception as exc:
                last_error = exc
                if attempt == 0:
                    try:
                        self.device.u2.uiautomator.start()
                        time.sleep(self.settle_seconds)
                    except Exception as start_exc:
                        last_error = start_exc
        raise AccountUiUnavailable(f"读取网易账号控件树失败：{last_error}") from last_error

    def _nodes(self, root, resource_id: str):
        return root.xpath(self._resource_xpath(resource_id))

    def current_account(self, root=None) -> str | None:
        """读取下拉框关闭时当前已选中的账号。"""
        root = root if root is not None else self.dump()
        # 下拉列表展开时会存在多条账号文本，不能误判为当前账号。
        if self._nodes(root, self.ACCOUNT_LIST_ID):
            return None
        for resource_id in self.ACCOUNT_TEXT_IDS:
            for node in self._nodes(root, resource_id):
                text = node.attrib.get("text", "").strip()
                if text:
                    return text
        return None

    def account_matches(self, actual: str | None, expected: str) -> bool:
        return self.normalize_account(actual) == self.normalize_account(expected)

    def nodes(self, root, resource_id: str):
        """查找带指定资源 ID 后缀的控件节点。"""
        return self._nodes(root, resource_id)

    def clickable_node(self, root, resource_id: str):
        """返回指定资源 ID 中第一个可见可点击控件。"""
        return next(
            (node for node in self._nodes(root, resource_id)
             if node.attrib.get("clickable") == "true"
             and node.attrib.get("enabled") == "true"),
            None,
        )

    def click_node(self, node, control_name: str) -> bool:
        """点击已从当前控件树中获取的节点。"""
        try:
            left, top, right, bottom = self._bounds(node)
            self.device.click(
                (left + right) // 2,
                (top + bottom) // 2,
                control_name=control_name,
            )
            return True
        except Exception:
            return False

    def click_resource(self, resource_id: str, control_name: str) -> bool:
        """按控件树资源 ID 点击控件并返回是否成功。"""
        try:
            root = self.dump()
            node = self.clickable_node(root, resource_id)
            return self.click_node(node, control_name) if node is not None else False
        except Exception:
            return False

    def current_user_center_account(self, root=None) -> str | None:
        """读取用户中心页面当前选中的网易账号。"""
        try:
            root = root if root is not None else self.dump()
            for node in self._nodes(root, "netease_mpay__login_center_username"):
                text = node.attrib.get("text", "").strip()
                if text:
                    return text
        except Exception:
            return None
        return None

    def _click_bounds(self, bounds: tuple[int, int, int, int], name: str) -> None:
        left, top, right, bottom = bounds
        self.device.click(
            (left + right) // 2,
            (top + bottom) // 2,
            control_name=name,
        )

    def _account_entries(self, root) -> list[AccountUiEntry]:
        list_nodes = self._nodes(root, self.ACCOUNT_LIST_ID)
        if not list_nodes:
            return []
        list_node = list_nodes[0]
        entries: list[AccountUiEntry] = []
        for resource_id in self.ACCOUNT_TEXT_IDS:
            for text_node in list_node.xpath(self._resource_xpath(resource_id)):
                account = text_node.attrib.get("text", "").strip()
                if not account:
                    continue
                clickable = next(
                    (node for node in text_node.iterancestors() if node.attrib.get("clickable") == "true"),
                    text_node,
                )
                entries.append(AccountUiEntry(account, self._bounds(clickable)))
        return entries

    def _open_list(self, root) -> bool:
        if self._nodes(root, self.ACCOUNT_LIST_ID):
            return True
        candidates = [
            node for node in self._nodes(root, self.ACCOUNT_ITEM_ID)
            if node.attrib.get("clickable") == "true"
        ]
        if len(candidates) != 1:
            return False
        self._click_bounds(self._bounds(candidates[0]), "网易账号列表展开")
        time.sleep(self.settle_seconds)
        return True

    def _at_list_bottom(self, root) -> bool:
        """“添加新账号”出现时，说明账号列表已经滑到底部。"""
        return bool(self._nodes(root, self.ADD_ACCOUNT_ID))

    def _scroll(self, root, *, toward_start: bool) -> bool:
        list_nodes = self._nodes(root, self.ACCOUNT_LIST_ID)
        if not list_nodes:
            return False
        # 向下搜索时在底部标志出现后立即停止，避免继续无效滑动。
        if not toward_start and self._at_list_bottom(root):
            return False
        left, top, right, bottom = self._bounds(list_nodes[0])
        x = (left + right) // 2
        height = bottom - top
        # 一屏最多约三个账号。每次随机移动 2～2.5 个账号：既避免跨过
        # 太多账号，也确保下一次控件树能看到足够明显的页面变化。
        distance = random.randint(*self.SCROLL_DISTANCE_RANGE)
        center_y = (top + bottom) // 2
        if toward_start:
            start, end, name = (
                (x, center_y - distance // 2),
                (x, center_y + distance // 2),
                "网易账号列表向上滑动",
            )
        else:
            start, end, name = (
                (x, center_y + distance // 2),
                (x, center_y - distance // 2),
                "网易账号列表向下滑动",
            )
        duration = random.uniform(*self.SCROLL_DURATION_RANGE)
        self.device.swipe(start, end, duration=duration, control_name=name)
        settle_seconds = max(self.settle_seconds, random.uniform(*self.SCROLL_SETTLE_RANGE))
        time.sleep(settle_seconds)
        return True

    def _select_visible(self, root, target: str, matcher) -> bool:
        for entry in self._account_entries(root):
            if not matcher(entry.account):
                continue
            self._click_bounds(entry.bounds, "网易账号选择")
            for _ in range(4):
                time.sleep(self.settle_seconds)
                if matcher(self.current_account()):
                    return True
            return False
        return False

    def select_account(self, target: str, matcher=None) -> bool:
        """从控件树账号列表中选中目标账号，并确认选择结果。"""
        matcher = matcher or (lambda actual: self.account_matches(actual, target))
        root = self.dump()
        if matcher(self.current_account(root)):
            return True
        if not self._open_list(root):
            return False

        root = self.dump()
        if self._select_visible(root, target, matcher):
            return True

        # 混合搜索：先从当前位置向下；未找到则回到顶部；最后从顶部
        # 向下完整扫描。既减少常见情况下的滑动，又保留完整遍历兜底。
        for toward_start in (False, True, False):
            previous = None
            unchanged_count = 0
            for _ in range(self.max_scrolls):
                root = self.dump()
                entries = self._account_entries(root)
                if not entries:
                    break
                # 小幅滑动后可见账号名称可能相同，因此同时比较纵坐标，
                # 并要求连续两次完全没有变化才认为到达边界。
                signature = tuple(
                    (self.normalize_account(entry.account), entry.bounds[1], entry.bounds[3])
                    for entry in entries
                )
                if signature == previous:
                    unchanged_count += 1
                    if unchanged_count >= 2:
                        break
                else:
                    unchanged_count = 0
                previous = signature
                if self._select_visible(root, target, matcher):
                    return True
                if not toward_start and self._at_list_bottom(root):
                    break
                if not self._scroll(root, toward_start=toward_start):
                    break
        return False
