# buckthorn/schema.py
from typing import Any, Optional, get_origin, get_type_hints
from buckthorn._core import CompiledFieldSpec, CompiledSchemaSpec

_UNDEFINED = Ellipsis

_SCHEMA_TYPE_MAP = {
    int: 1,    # TYPE_INT
    float: 2,  # TYPE_FLOAT
    str: 3,    # TYPE_STR
    bool: 5,   # TYPE_BOOL
    list: 6,   # TYPE_LIST
}


class FieldInfo:
    def __init__(
        self,
        default: Any = _UNDEFINED,
        *,
        ge: Optional[float] = None,
        le: Optional[float] = None,
        gt: Optional[float] = None,
        lt: Optional[float] = None,
        min_length: Optional[int] = None,
        max_length: Optional[int] = None,
    ):
        self.default = default
        self.required = default is _UNDEFINED
        self.ge = ge
        self.le = le
        self.gt = gt
        self.lt = lt
        self.min_length = min_length
        self.max_length = max_length


def Field(default: Any = _UNDEFINED, **kwargs: Any) -> Any:
    return FieldInfo(default=default, **kwargs)


class Schema:
    __bt_schema_spec__: CompiledSchemaSpec

    def __init_subclass__(cls, **kwargs: Any):
        super().__init_subclass__(**kwargs)
        hints = get_type_hints(cls)
        compiled_fields: list[CompiledFieldSpec] = []

        for field_name, annotation in hints.items():
            if field_name.startswith("__"):
                continue

            origin = get_origin(annotation) or annotation
            field_type = _SCHEMA_TYPE_MAP.get(origin, 3)

            raw_default = getattr(cls, field_name, _UNDEFINED)
            if isinstance(raw_default, FieldInfo):
                finfo = raw_default
                # Clean descriptor off the class so instance attribute lookup is unobstructed
                delattr(cls, field_name)
            else:
                finfo = FieldInfo(default=raw_default)

            compiled_fields.append(
                CompiledFieldSpec(
                    name=field_name,
                    field_type=field_type,
                    required=finfo.required,
                    default_val=None if finfo.default is _UNDEFINED else finfo.default,
                    ge=finfo.ge,
                    le=finfo.le,
                    gt=finfo.gt,
                    lt=finfo.lt,
                    min_length=finfo.min_length,
                    max_length=finfo.max_length,
                )
            )

        cls.__bt_schema_spec__ = CompiledSchemaSpec(cls, compiled_fields)

    def __init__(self, **kwargs: Any):
        for fspec in self.__bt_schema_spec__.fields:
            if fspec.name in kwargs:
                setattr(self, fspec.name, kwargs[fspec.name])
            elif not fspec.required:
                val = list(fspec.default_val) if fspec.is_list_default else fspec.default_val
                setattr(self, fspec.name, val)
            else:
                raise ValueError(f"Missing required schema field: {fspec.name}")

    def model_dump(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in self.__bt_schema_spec__.fields}
