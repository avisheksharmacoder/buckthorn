# buckthorn/schema.py
import types
from typing import Any, Optional, Union, get_args, get_origin, get_type_hints
from buckthorn._core import CompiledFieldSpec, CompiledSchemaSpec

_UNDEFINED = Ellipsis

_SCHEMA_TYPE_MAP = {
    int: 1,    # TYPE_INT
    float: 2,  # TYPE_FLOAT
    str: 3,    # TYPE_STR
    bool: 5,   # TYPE_BOOL
    list: 6,   # TYPE_LIST
}

_UNION_TYPES = (Union, getattr(types, "UnionType", Union))

# Global registry mapping both pure-Python Schema classes and compiled Cython cdef classes
SCHEMA_SPEC_REGISTRY: dict[type, CompiledSchemaSpec] = {}


def _unwrap_optional_type(annotation: Any) -> Any:
    origin = get_origin(annotation)
    if origin in _UNION_TYPES:
        non_none = [a for a in get_args(annotation) if a is not type(None)]
        if len(non_none) == 1:
            return non_none[0]
    return annotation


def unwrap_type_annotation(annotation: Any) -> tuple[Any, bool]:
    """
    Unwraps typing.Union / Optional[T] / T | None into (inner_type, is_optional).
    Resolves origin for parameterized types like list[int] -> list.
    """
    origin = get_origin(annotation)
    is_union = origin in _UNION_TYPES
    if is_union:
        args = [a for a in get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            inner_origin = get_origin(args[0]) or args[0]
            return inner_origin, True
    return origin or annotation, False


def get_schema_spec(cls_or_obj: Any) -> Optional[CompiledSchemaSpec]:
    if isinstance(cls_or_obj, type):
        spec = SCHEMA_SPEC_REGISTRY.get(cls_or_obj)
        if spec is not None:
            return spec
    if isinstance(cls_or_obj, str):
        for cls, spec in SCHEMA_SPEC_REGISTRY.items():
            if getattr(cls, "__name__", None) == cls_or_obj:
                return spec
    spec = getattr(cls_or_obj, "__bt_schema_spec__", None)
    if spec is not None:
        return spec
    if not isinstance(cls_or_obj, type) and cls_or_obj is not None:
        return SCHEMA_SPEC_REGISTRY.get(type(cls_or_obj))
    return None


def register_compiled_schema(
    schema_cls: type,
    field_defs: list[tuple[Any, ...]],
) -> CompiledSchemaSpec:
    """
    Registers a Cython `cdef class` struct in the C yyjson validator/serializer table.
    Each tuple in `field_defs` is:
      (name, field_type, required, default_val, ge, le, gt, lt, min_length, max_length[, sub_schema])
    """
    compiled_fields = [
        CompiledFieldSpec(
            name=f[0],
            field_type=f[1],
            required=f[2],
            default_val=f[3],
            ge=f[4],
            le=f[5],
            gt=f[6],
            lt=f[7],
            min_length=f[8],
            max_length=f[9],
            sub_schema=f[10] if len(f) > 10 else None,
        )
        for f in field_defs
    ]
    spec = CompiledSchemaSpec(schema_cls, compiled_fields)
    SCHEMA_SPEC_REGISTRY[schema_cls] = spec
    return spec


class FieldInfo:
    __slots__ = ("default", "required", "ge", "le", "gt", "lt", "min_length", "max_length")

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
    _bt_field_names: tuple[str, ...] = ()

    def __init_subclass__(cls, **kwargs: Any):
        super().__init_subclass__(**kwargs)
        hints = get_type_hints(cls)
        compiled_fields: list[CompiledFieldSpec] = []
        field_names: list[str] = []

        for field_name, raw_annotation in hints.items():
            if field_name.startswith("__") or field_name == "_bt_field_names":
                continue

            unwrapped = _unwrap_optional_type(raw_annotation)
            origin = get_origin(unwrapped) or unwrapped
            sub_schema_spec: Optional[CompiledSchemaSpec] = get_schema_spec(unwrapped)

            if sub_schema_spec is not None:
                field_type = 7  # TYPE_SCHEMA (nested Schema object)
            elif origin is list:
                field_type = 6  # TYPE_LIST
                list_args = get_args(unwrapped)
                if list_args:
                    elem_type = _unwrap_optional_type(list_args[0])
                    sub_schema_spec = get_schema_spec(elem_type)
            else:
                field_type = _SCHEMA_TYPE_MAP.get(origin, 3)

            raw_default = getattr(cls, field_name, _UNDEFINED)
            if isinstance(raw_default, FieldInfo):
                finfo = raw_default
                delattr(cls, field_name)
            else:
                finfo = FieldInfo(default=raw_default)

            # Allow `Optional[T]` without an explicit `= None` to default to None
            is_optional = (
                get_origin(raw_annotation) in _UNION_TYPES
                and type(None) in get_args(raw_annotation)
            )
            if is_optional and finfo.required and raw_default is _UNDEFINED:
                finfo.required = False
                finfo.default = None

            field_names.append(field_name)
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
                    sub_schema=sub_schema_spec,
                )
            )

        spec = CompiledSchemaSpec(cls, compiled_fields)
        cls.__bt_schema_spec__ = spec
        cls._bt_field_names = tuple(field_names)
        SCHEMA_SPEC_REGISTRY[cls] = spec

    def __init__(self, **kwargs: Any):
        for fspec in self.__bt_schema_spec__.fields:
            fname = fspec.name
            if fname in kwargs:
                val = kwargs[fname]
                if val is not None and fspec.sub_schema is not None:
                    sub_cls = fspec.sub_schema.schema_cls
                    if fspec.field_type == 7 and isinstance(val, dict):
                        val = sub_cls(**val)
                    elif fspec.field_type == 6 and isinstance(val, list):
                        val = [sub_cls(**x) if isinstance(x, dict) else x for x in val]
                setattr(self, fname, val)
            elif not fspec.required:
                dval = fspec.default_val
                if fspec.is_list_default or isinstance(dval, (dict, set)):
                    dval = dval.copy()
                setattr(self, fname, dval)
            else:
                raise ValueError(f"Missing required schema field: {fname}")

    def model_dump(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for name in self._bt_field_names:
            val = getattr(self, name)
            if hasattr(val, "model_dump"):
                out[name] = val.model_dump()
            elif isinstance(val, list):
                out[name] = [
                    item.model_dump() if hasattr(item, "model_dump") else item
                    for item in val
                ]
            else:
                out[name] = val
        return out
