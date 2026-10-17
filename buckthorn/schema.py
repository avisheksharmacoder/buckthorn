# buckthorn/schema.py
import types
from typing import Any, Optional, Type, Union, get_args, get_origin, get_type_hints
from buckthorn._core import CompiledFieldSpec, CompiledSchemaSpec

_UNDEFINED = Ellipsis

_SCHEMA_TYPE_MAP = {
    int: 1,    # TYPE_INT
    float: 2,  # TYPE_FLOAT
    str: 3,    # TYPE_STR
    bool: 5,   # TYPE_BOOL
    list: 6,   # TYPE_LIST
}


def unwrap_type_annotation(annotation: Any) -> tuple[Any, bool]:
    """
    Unwraps typing.Union / Optional[T] / T | None into (inner_type, is_optional).
    Resolves origin for parameterized types like list[int] -> list.
    """
    origin = get_origin(annotation)
    is_union = origin is Union or (hasattr(types, "UnionType") and origin is types.UnionType)
    if is_union:
        args = [a for a in get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            inner_origin = get_origin(args[0]) or args[0]
            return inner_origin, True
    return origin or annotation, False

# Global registry mapping both pure-Python Schema classes and compiled Cython cdef classes
SCHEMA_SPEC_REGISTRY: dict[type, CompiledSchemaSpec] = {}


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
    field_defs: list[tuple[str, int, bool, Any, Any, Any, Any, Any, Any, Any]],
) -> CompiledSchemaSpec:
    """
    Registers a Cython `cdef class` struct in the C yyjson validator/serializer table.
    Each tuple in `field_defs` is:
      (name, field_type, required, default_val, ge, le, gt, lt, min_length, max_length)
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
        )
        for f in field_defs
    ]
    spec = CompiledSchemaSpec(schema_cls, compiled_fields)
    SCHEMA_SPEC_REGISTRY[schema_cls] = spec
    return spec


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

            unwrapped_ann, is_optional = unwrap_type_annotation(annotation)
            field_type = _SCHEMA_TYPE_MAP.get(unwrapped_ann, 3)

            raw_default = getattr(cls, field_name, _UNDEFINED)
            if isinstance(raw_default, FieldInfo):
                finfo = raw_default
                # Clean descriptor off the class so instance attribute lookup is unobstructed
                delattr(cls, field_name)
                if is_optional and finfo.required:
                    finfo.required = False
                    if finfo.default is _UNDEFINED:
                        finfo.default = None
            else:
                default_val = raw_default
                if is_optional and default_val is _UNDEFINED:
                    default_val = None
                finfo = FieldInfo(default=default_val)

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

        spec = CompiledSchemaSpec(cls, compiled_fields)
        cls.__bt_schema_spec__ = spec
        SCHEMA_SPEC_REGISTRY[cls] = spec

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
