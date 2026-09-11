"""Shared form helpers for explicit public/private multi-account task configuration."""

from __future__ import annotations


def parse_config_mode(value) -> str | None:
    """兼容原生枚举组件可能提交的值、键名和中文显示文本。"""
    normalized = str(getattr(value, "value", value) or "").strip().lower()
    return {
        "public": "public",
        "private": "private",
        "公共配置": "public",
        "私有配置": "private",
    }.get(normalized)


def config_mode_value(entry) -> str:
    mode = getattr(entry, "config_mode", "private")
    return getattr(mode, "value", mode)


def with_config_mode_group(task_args: dict, entry) -> dict:
    """Prepend a native OAS enum field so OASX can switch configuration source."""
    return {
        "multi_account_config_source": [{
            "name": "config_mode",
            "title": "config_mode",
            "description": "config_mode_help",
            "default": "private",
            "value": config_mode_value(entry),
            "type": "enum",
            "enumEnum": ["public", "private"],
        }],
        **task_args,
    }


def is_config_mode_field(group: str, argument: str) -> bool:
    return group == "multi_account_config_source" and argument == "config_mode"
