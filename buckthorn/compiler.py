# buckthorn/compiler.py
import ast
import importlib.util
import platform
import sysconfig
from pathlib import Path
from types import ModuleType
from typing import Any
from setuptools import Distribution, Extension
from Cython.Build import cythonize

_ANNOTATION_TO_CYTHON_FIELD = {
    "int": ("long long", 1),
    "float": ("double", 2),
    "str": ("str", 3),
    "bool": ("bint", 5),
    "list": ("list", 6),
}

_ANNOTATION_TO_CYTHON_LOCAL = {
    "int": "cython.longlong",
    "float": "cython.double",
    "bool": "cython.bint",
}


def _contains_async_construct(node: ast.AST) -> bool:
    """Returns True if the function body contains any await, async for, or async with."""
    for child in ast.walk(node):
        if isinstance(child, (ast.Await, ast.AsyncFor, ast.AsyncWith)):
            return True
    return False


def _extract_type_name(annotation_node: ast.AST | None) -> str:
    if annotation_node is None:
        return "object"
    if isinstance(annotation_node, ast.Name):
        return annotation_node.id
    if isinstance(annotation_node, ast.Subscript) and isinstance(annotation_node.value, ast.Name):
        return annotation_node.value.id
    return "object"


def _parse_field_constraints(value_node: ast.AST | None) -> dict[str, Any]:
    info: dict[str, Any] = {
        "required": True,
        "default": "None",
        "ge": "None",
        "le": "None",
        "gt": "None",
        "lt": "None",
        "min_length": "None",
        "max_length": "None",
    }
    if value_node is None:
        return info

    if (
        isinstance(value_node, ast.Call)
        and isinstance(value_node.func, ast.Name)
        and value_node.func.id == "Field"
    ):
        if value_node.args:
            arg0_src = ast.unparse(value_node.args[0])
            if arg0_src != "...":
                info["required"] = False
                info["default"] = arg0_src
        for kw in value_node.keywords:
            if kw.arg == "default":
                val_src = ast.unparse(kw.value)
                if val_src != "...":
                    info["required"] = False
                    info["default"] = val_src
            elif kw.arg in info:
                info[kw.arg] = ast.unparse(kw.value)
    else:
        info["required"] = False
        info["default"] = ast.unparse(value_node)

    return info


class _LocalAnnAssignLowerer(ast.NodeTransformer):
    """Recursively lowers annotated local assignments inside function blocks into @cython.locals."""

    def __init__(self, cython_locals: dict[str, str]):
        self.cython_locals = cython_locals

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:
        return node  # Do not descend into nested function scopes

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AST:
        return node

    def visit_ClassDef(self, node: ast.ClassDef) -> ast.AST:
        return node

    def visit_AnnAssign(self, node: ast.AnnAssign) -> ast.AST | None:
        if isinstance(node.target, ast.Name):
            tname = _extract_type_name(node.annotation)
            if tname in _ANNOTATION_TO_CYTHON_LOCAL:
                self.cython_locals[node.target.id] = _ANNOTATION_TO_CYTHON_LOCAL[tname]
            if node.value is not None:
                assign_stmt = ast.Assign(targets=[node.target], value=node.value)
                return ast.copy_location(assign_stmt, node)
            return None
        return node


def _lower_schema_class_to_pyx(cls_node: ast.ClassDef) -> str:
    cls_name = cls_node.name
    cdef_lines: list[str] = []
    init_lines: list[str] = []
    dump_entries: list[str] = []
    spec_tuples: list[str] = []

    for stmt in cls_node.body:
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            fname = stmt.target.id
            tname = _extract_type_name(stmt.annotation)
            c_type, type_id = _ANNOTATION_TO_CYTHON_FIELD.get(tname, ("object", 3))
            fmeta = _parse_field_constraints(stmt.value)

            cdef_lines.append(f"    cdef public {c_type} {fname}")
            if fmeta["required"]:
                init_lines.append(
                    f"        if '{fname}' in kwargs:\n"
                    f"            self.{fname} = kwargs['{fname}']"
                )
            else:
                def_expr = fmeta["default"]
                if type_id == 6 and def_expr != "None":
                    init_lines.append(
                        f"        self.{fname} = list(kwargs['{fname}']) if '{fname}' in kwargs else list({def_expr})"
                    )
                else:
                    init_lines.append(
                        f"        self.{fname} = kwargs.get('{fname}', {def_expr})"
                    )

            dump_entries.append(f"'{fname}': self.{fname}")
            spec_tuples.append(
                f"('{fname}', {type_id}, {fmeta['required']}, {fmeta['default']}, "
                f"{fmeta['ge']}, {fmeta['le']}, {fmeta['gt']}, {fmeta['lt']}, "
                f"{fmeta['min_length']}, {fmeta['max_length']})"
            )

    spec_var = f"_BT_SCHEMA_SPEC_{cls_name}"
    lines = [
        f"{spec_var} = None",
        "@cython.freelist(4096)",
        f"cdef class {cls_name}:",
        *cdef_lines,
        "",
        "    def __init__(self, **kwargs):",
        *(init_lines if init_lines else ["        pass"]),
        "",
        "    @property",
        "    def __bt_schema_spec__(self):",
        f"        return {spec_var}",
        "",
        "    cpdef dict model_dump(self):",
        f"        return {{{', '.join(dump_entries)}}}",
        "",
        f"{spec_var} = _bt_register_compiled_schema({cls_name}, [{', '.join(spec_tuples)}])",
    ]
    return "\n".join(lines)


def _is_sentinel_default(def_node: ast.AST | None) -> bool:
    if def_node is None:
        return False
    if isinstance(def_node, ast.Call):
        return True
    if isinstance(def_node, ast.Constant) and def_node.value is None:
        return True
    return False


def _lower_function_to_pyx(fn_node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    is_async = isinstance(fn_node, ast.AsyncFunctionDef)
    can_elide_async = is_async and not _contains_async_construct(fn_node)

    cython_locals: dict[str, str] = {}
    num_args = len(fn_node.args.args)
    num_defaults = len(fn_node.args.defaults)
    default_offset = num_args - num_defaults

    for i, arg in enumerate(fn_node.args.args):
        tname = _extract_type_name(arg.annotation)
        if tname in _ANNOTATION_TO_CYTHON_LOCAL:
            if i >= default_offset:
                def_node = fn_node.args.defaults[i - default_offset]
                if _is_sentinel_default(def_node):
                    continue
            cython_locals[arg.arg] = _ANNOTATION_TO_CYTHON_LOCAL[tname]

    for j, kwarg in enumerate(fn_node.args.kwonlyargs):
        tname = _extract_type_name(kwarg.annotation)
        if tname in _ANNOTATION_TO_CYTHON_LOCAL:
            kw_def = fn_node.args.kw_defaults[j] if j < len(fn_node.args.kw_defaults) else None
            if _is_sentinel_default(kw_def):
                continue
            cython_locals[kwarg.arg] = _ANNOTATION_TO_CYTHON_LOCAL[tname]

    lowerer = _LocalAnnAssignLowerer(cython_locals)
    new_body: list[ast.stmt] = []
    for stmt in fn_node.body:
        transformed = lowerer.visit(stmt)
        if transformed is not None:
            new_body.append(transformed)
    if not new_body:
        new_body.append(ast.Pass())

    decorators_src = [f"@{ast.unparse(d)}" for d in fn_node.decorator_list]
    if cython_locals:
        locals_kv = ", ".join(f"{k}={v}" for k, v in cython_locals.items())
        decorators_src.append(f"@cython.locals({locals_kv})")

    if can_elide_async or not is_async:
        clean_fn: ast.AST = ast.FunctionDef(
            name=fn_node.name,
            args=fn_node.args,
            body=new_body,
            decorator_list=[],
            returns=fn_node.returns,
            type_comment=fn_node.type_comment,
        )
    else:
        clean_fn = ast.AsyncFunctionDef(
            name=fn_node.name,
            args=fn_node.args,
            body=new_body,
            decorator_list=[],
            returns=fn_node.returns,
            type_comment=fn_node.type_comment,
        )
    ast.fix_missing_locations(clean_fn)
    fn_src = ast.unparse(clean_fn)

    out_blocks = [*decorators_src, fn_src]

    if can_elide_async:
        arg_names = [a.arg for a in fn_node.args.args]
        call_args = ", ".join(arg_names)
        out_blocks.append(
            f"\ncpdef object _sync_fast_{fn_node.name}({call_args}):\n"
            f"    return {fn_node.name}({call_args})"
        )

    return "\n".join(out_blocks)


def transpile_module_to_pyx(source_code: str) -> str:
    """
    Transpiles a developer's standard Python Buckthorn module into an optimized
    Cython `.pyx` source string with `cdef class` Schemas and Coroutine Elision.
    """
    tree = ast.parse(source_code)
    header = [
        "# cython: language_level=3, boundscheck=False, wraparound=False, cdivision=True, initializedcheck=False, infer_types=True, binding=True, annotation_typing=False",
        "cimport cython",
        "from buckthorn.schema import register_compiled_schema as _bt_register_compiled_schema",
        "",
    ]

    chunks: list[str] = list(header)

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            base_names = [_extract_type_name(b) for b in node.bases]
            if "Schema" in base_names:
                chunks.append(_lower_schema_class_to_pyx(node))
                chunks.append("")
                continue

        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            chunks.append(_lower_function_to_pyx(node))
            chunks.append("")
            continue

        chunks.append(ast.unparse(node))
        chunks.append("")

    return "\n".join(chunks)


def compile_pyx_module(
    pyx_code: str,
    module_name: str = "buckthorn_compiled_app",
    workdir: str | Path = ".",
) -> ModuleType:
    """Compiles transpiled `.pyx` code into a native `.so` shared library and loads it."""
    if not module_name.isidentifier():
        raise ValueError(f"Invalid Cython module_name: {module_name!r}")

    work_path = Path(workdir).resolve()
    work_path.mkdir(parents=True, exist_ok=True)

    pyx_file = work_path / f"{module_name}.pyx"
    pyx_file.write_text(pyx_code, encoding="utf-8")

    compile_args = ["-O3", "-march=native", "-Wno-unused-function"]
    if platform.machine() in ("x86_64", "AMD64"):
        compile_args.append("-msse4.2")

    ext = Extension(
        name=module_name,
        sources=[str(pyx_file)],
        extra_compile_args=compile_args,
    )
    ext_modules = cythonize(
        [ext],
        compiler_directives={
            "language_level": "3",
            "boundscheck": False,
            "wraparound": False,
            "cdivision": True,
            "initializedcheck": False,
            "infer_types": True,
            "binding": True,
            "annotation_typing": False,
        },
        quiet=True,
    )

    dist = Distribution({"name": module_name, "ext_modules": ext_modules})
    cmd = dist.get_command_obj("build_ext")
    cmd.ensure_finalized()
    cmd.build_lib = str(work_path)
    cmd.build_temp = str(work_path / "build_tmp")
    cmd.run()

    ext_suffix = sysconfig.get_config_var("EXT_SUFFIX") or ".so"
    exact_so = work_path / f"{module_name}{ext_suffix}"
    if exact_so.exists():
        so_path = exact_so
    else:
        so_files = list(work_path.glob(f"{module_name}.*.so")) or list(work_path.glob(f"{module_name}.so"))
        if not so_files:
            raise RuntimeError(f"Failed to compile Cython extension for {module_name}")
        so_path = so_files[0]

    spec = importlib.util.spec_from_file_location(module_name, so_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load compiled extension from {so_path}")

    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
