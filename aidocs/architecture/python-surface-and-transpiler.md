# Domain 3: Python Surface, Schema Registry & AST-to-Cython Transpiler

## 1. Scope & Files
- `buckthorn/app.py` — `Buckthorn` main class, signature compiler (`_compile_callable_params`), `lifespan` manager, and `include_router`.
- `buckthorn/schema.py` — `Schema` base class, `Field()` constraints, `SCHEMA_SPEC_REGISTRY`, and `register_compiled_schema()`.
- `buckthorn/routing.py` — Modular `APIRouter` with compile-time prefix, tag, and dependency flattening.
- `buckthorn/openapi.py` — OpenAPI 3.1 JSON generator and static HTML builders for Swagger UI (`/docs`) and Scalar (`/scalar`).
- `buckthorn/compiler.py` — AST-to-Cython transpiler (`transpile_module_to_pyx`) and native `.so` compiler (`compile_pyx_module`).
- `buckthorn/cli.py` — CLI entrypoints (`buckthorn dev`, `buckthorn build --prod`, `buckthorn run --workers N`).

---

## 2. Dual-Mode `Schema` Architecture (Dev Mode vs. Compiled Prod Mode)
Buckthorn supports two execution modes for `Schema` models:
1. **Dev Mode (`buckthorn/schema.py`):**
   - User subclasses `class Order(Schema):`.
   - `Schema.__init_subclass__` inspects PEP 484 type annotations and `Field(...)` defaults, builds a `CompiledSchemaSpec`, sets `cls.__bt_schema_spec__ = spec`, and registers `SCHEMA_SPEC_REGISTRY[cls] = spec`.
2. **Prod Mode (`buckthorn/compiler.py`):**
   - `transpile_module_to_pyx()` rewrites every `class Order(Schema):` AST node into a Cython `@cython.freelist(4096) cdef class Order:` with unboxed C attributes (`cdef public long long`, `cdef public double`, `cdef public str`, `cdef public bint`, `cdef public list`).
   - Because Cython `cdef class` type objects (`PyTypeObject`) are immutable and disallow setting `Order.__bt_schema_spec__` on the class, the generated `.pyx` file calls `_bt_register_compiled_schema(Order, [...])` to populate `SCHEMA_SPEC_REGISTRY[Order]` and defines a `@property def __bt_schema_spec__(self):` on instances.
   - **Rule:** Always use `get_schema_spec(cls_or_obj)` in `app.py` and `openapi.py` instead of `issubclass(x, Schema)`.

---

## 3. AST-to-Cython Transpiler Rules (`buckthorn/compiler.py`)
When modifying `buckthorn/compiler.py`:
1. **Coroutine Elision (`_contains_async_construct`):**
   - An `async def` route handler is converted into a synchronous `def` function (plus a `cpdef object _sync_fast_<name>` wrapper) **ONLY IF** `ast.walk(fn_node)` contains zero `ast.Await`, `ast.AsyncFor`, or `ast.AsyncWith` nodes.
   - If any `await` is present in the function body, `async def` MUST be preserved untouched.
2. **Local Variable C Lowering (`@cython.locals`):**
   - Type-annotated function arguments (`x: int`) and annotated local assignments (`total: float = ...`) are collected into `@cython.locals(x=cython.longlong, total=cython.double)`.
   - Annotated assignments inside the function body must be converted from `ast.AnnAssign` to `ast.Assign` in the transformed AST so Cython does not complain about duplicate type declarations alongside `@cython.locals`.
