"""Multiple independently scheduled normal multi-account instances in one OAS task."""
from typing import Any

from pydantic import Field, model_serializer, model_validator

from tasks.Component.config_base import ConfigBase
from tasks.Component.config_scheduler import Scheduler
from tasks.Component.MultiAccount.multi_account_config import (
    dump_model,
    load_indexed_models,
    serialize_indexed_models,
)
from tasks.MultiAccountRepeatNewNormal.config import MultiAccountRepeatNewNormal


class NormalInstance(ConfigBase):
    instance_id: str = Field(default="", title="实例标识")
    name: str = Field(default="", title="实例名称")
    normal: MultiAccountRepeatNewNormal = Field(default_factory=MultiAccountRepeatNewNormal)


class MultiAccountRepeatNewCollection(ConfigBase):
    """Only instances within this section can optionally share task runtime records."""

    scheduler: Scheduler = Field(default_factory=Scheduler)
    share_runtime_records: bool = Field(
        default=False, title="同一账号跨实例共享任务运行记录"
    )
    instance_list: list[NormalInstance] = Field(default_factory=list)
    shared_runtime_records: dict[str, dict[str, dict[str, Any]]] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def read_instances(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        data["instance_list"] = load_indexed_models(data, "instance_list", NormalInstance)
        return data

    @model_validator(mode="after")
    def validate_instances(self):
        ids: set[str] = set()
        names: set[str] = set()
        for instance in self.instance_list:
            instance.name = instance.name.strip()
            if not instance.instance_id or not instance.name:
                raise ValueError("集合实例需要非空标识和名称")
            if instance.instance_id in ids or instance.name.casefold() in names:
                raise ValueError("集合内实例标识和名称不能重复")
            ids.add(instance.instance_id)
            names.add(instance.name.casefold())
        return self

    @model_serializer()
    def serialize(self) -> dict[str, Any]:
        data: dict[str, Any] = {}
        for key, value in self.__dict__.items():
            if key == "instance_list":
                serialize_indexed_models(data, key, value)
            else:
                data[key] = dump_model(value)
        return data
