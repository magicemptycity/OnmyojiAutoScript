from __future__ import annotations

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

    def _scroll(self, root, *, toward_start: bool) -> bool:
        list_nodes = self._nodes(root, self.ACCOUNT_LIST_ID)
        if not list_nodes:
            return False
        left, top, right, bottom = self._bounds(list_nodes[0])
        x = (left + right) // 2
        height = bottom - top
        if toward_start:
            start, end, name = (
                (x, top + height // 3),
                (x, bottom - height // 5),
                "网易账号列表向上滑动",
            )
        else:
            start, end, name = (
                (x, bottom - height // 5),
                (x, top + height // 3),
                "网易账号列表向下滑动",
            )
        self.device.swipe(start, end, duration=0.35, control_name=name)
        time.sleep(self.settle_seconds)
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

        # 先回到列表顶端，再向底部逐页搜索；重复页面意味着到达边界。
        for toward_start in (True, False):
            previous: tuple[str, ...] | None = None
            for _ in range(self.max_scrolls):
                root = self.dump()
                entries = self._account_entries(root)
                signature = tuple(self.normalize_account(entry.account) for entry in entries)
                if signature == previous or not entries:
                    break
                previous = signature
                if self._select_visible(root, target, matcher):
                    return True
                if not self._scroll(root, toward_start=toward_start):
                    break
        return False
