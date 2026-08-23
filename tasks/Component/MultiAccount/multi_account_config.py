"""多账号任务共用的配置处理工具。"""

import re
from typing import Any

from pydantic import BaseModel, ValidationError

from deploy.logger import logger


def as_dict(value: Any) -> dict:
    """把字典或 Pydantic 配置对象转换成普通字典。"""
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, BaseModel):
        return value.model_dump()
    return {}


def coerce_model(value: Any, model: type[BaseModel]) -> BaseModel:
    """把一个配置项转换成指定的 Pydantic 模型。"""
    if isinstance(value, model):
        return value
    if isinstance(value, dict):
        return model(**value)
    raise TypeError(f"配置项必须是字典，实际类型为 {type(value).__name__}")


def load_indexed_models(
    data: dict,
    field_name: str,
    model: type[BaseModel],
) -> list[BaseModel]:
    """读取列表配置，同时兼容 ``field_name_1`` 形式的扁平字段。

    OASX 会把列表中的每一项作为一个独立配置组返回，例如：
    ``account_list_1``、``private_config_1``。任务配置模型内部仍然使用列表，
    因此所有多账号任务都应该通过这个函数统一完成扁平字段还原。
    """
    raw_values = data.get(field_name, [])
    values = list(raw_values) if isinstance(raw_values, list) else []
    pattern = re.compile(rf"^{re.escape(field_name)}_(\d+)$")

    indexed_values = []
    for key in list(data):
        match = pattern.fullmatch(key)
        if match:
            indexed_values.append((int(match.group(1)), key, data.pop(key)))

    for index_number, key, value in sorted(indexed_values):
        index = index_number - 1
        if index < 0:
            logger.warning("忽略无效的多账号配置项：%s", key)
            continue

        while len(values) <= index:
            values.append(model())

        try:
            values[index] = coerce_model(value, model)
        except (ValidationError, TypeError, ValueError) as exc:
            logger.warning("忽略无效的多账号配置项 %s：%s", key, exc)
            values[index] = model()

    normalized = []
    for value in values:
        try:
            normalized.append(coerce_model(value, model))
        except (ValidationError, TypeError, ValueError) as exc:
            logger.warning("多账号配置列表中存在无效项，使用默认值：%s", exc)
            normalized.append(model())
    return normalized


def compact_empty_account_models(
    accounts: list[BaseModel],
    parallel_lists: list[list[BaseModel]],
) -> None:
    """移除真正的空账号，并同步移动其下标对应的私有配置。

    公共账号库不参与此处理。公共账号序号大于 0 的账号行即使当前引用无效，
    也必须保留，避免误删或改变公共账号引用关系。
    """
    keep_indexes = [
        index
        for index, account in enumerate(accounts)
        if not (
            not str(getattr(account, "character", "") or "").strip()
            and not str(getattr(account, "svr", "") or "").strip()
            and getattr(account, "shared_account_index", 0) == 0
        )
    ]
    if len(keep_indexes) == len(accounts):
        return

    accounts[:] = [accounts[index] for index in keep_indexes]
    for values in parallel_lists:
        values[:] = [
            values[index]
            for index in keep_indexes
            if index < len(values)
        ]


def pad_parallel_models(
    model_lists: dict[str, list[BaseModel]],
    count: int,
    model_types: dict[str, type[BaseModel]] | None = None,
) -> int:
    """把多个按账号下标对应的配置列表补齐到相同长度。"""
    total_count = max([count, *(len(values) for values in model_lists.values())])
    for field_name, values in model_lists.items():
        model_type = None
        if model_types:
            model_type = model_types.get(field_name)
        if model_type is None and values:
            model_type = values[0].__class__
        if model_type is None:
            raise ValueError(f"无法从空配置列表推断 {field_name} 的模型类型")
        while len(values) < total_count:
            values.append(model_type())
    return total_count

def dump_model(value: Any) -> Any:
    """导出 Pydantic 模型，普通值保持不变。"""
    if isinstance(value, BaseModel):
        return value.model_dump()
    return value


def serialize_indexed_models(
    data: dict,
    field_name: str,
    values: list[Any],
) -> None:
    """把普通列表序列化成 OASX 使用的扁平字段。"""
    for index, value in enumerate(values, start=1):
        data[f"{field_name}_{index}"] = dump_model(value)


def serialize_account_list(
    data: dict,
    field_name: str,
    accounts: list[Any],
    private_configs: dict[str, tuple[list[Any], str, type[BaseModel]]],
) -> None:
    """序列化账号列表，并按账号开关输出对应的私有配置组。

    ``private_configs`` 的格式为：
    ``{配置字段名: (配置列表, 账号开关字段名, 配置模型类型)}``。
    例如，账号开启 ``enable_private_config`` 后，才输出
    ``private_config_1``。
    """
    for index, account in enumerate(accounts, start=1):
        data[f"{field_name}_{index}"] = dump_model(account)
        for private_name, (values, enable_field, model) in private_configs.items():
            if not getattr(account, enable_field, False):
                continue
            value = values[index - 1] if index - 1 < len(values) else model()
            data[f"{private_name}_{index}"] = dump_model(value)
