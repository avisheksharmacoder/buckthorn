# buckthorn/openapi.py
import html
import json
from typing import Any, Type
from buckthorn._core import CompiledFieldSpec, CompiledParamSpec, CompiledSchemaSpec
from buckthorn.schema import Schema, get_schema_spec

_OPENAPI_TYPE_MAP = {
    1: "integer",  # TYPE_INT
    2: "number",   # TYPE_FLOAT
    3: "string",   # TYPE_STR
    5: "boolean",  # TYPE_BOOL
    6: "array",    # TYPE_LIST
}

_SOURCE_IN_MAP = {
    1: "path",
    2: "query",
    3: "header",
    4: "cookie",
}


def _schema_spec_to_json_schema(spec: CompiledSchemaSpec) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    required: list[str] = []

    for i in range(spec.num_fields):
        f: CompiledFieldSpec = spec.fields[i]
        prop: dict[str, Any] = {"type": _OPENAPI_TYPE_MAP.get(f.field_type, "string")}
        if f.field_type == 6:
            prop["items"] = {"type": "string"}

        if f.has_ge:
            prop["minimum"] = f.int_ge if f.field_type == 1 else f.float_ge
        if f.has_le:
            prop["maximum"] = f.int_le if f.field_type == 1 else f.float_le
        if f.has_gt:
            prop["exclusiveMinimum"] = f.int_gt if f.field_type == 1 else f.float_gt
        if f.has_lt:
            prop["exclusiveMaximum"] = f.int_lt if f.field_type == 1 else f.float_lt
        if f.has_min_len:
            prop["minLength"] = f.min_len
        if f.has_max_len:
            prop["maxLength"] = f.max_len

        if f.required:
            required.append(f.name)
        elif f.default_val is not None:
            prop["default"] = f.default_val

        properties[f.name] = prop

    schema_dict: dict[str, Any] = {
        "title": spec.schema_cls.__name__,
        "type": "object",
        "properties": properties,
    }
    if required:
        schema_dict["required"] = required
    return schema_dict


def build_openapi_schema(
    title: str,
    version: str,
    routes_meta: list[dict[str, Any]],
) -> dict[str, Any]:
    paths: dict[str, Any] = {}
    components_schemas: dict[str, Any] = {}

    for r in routes_meta:
        path_str: str = r["path"]
        if path_str in ("/openapi.json", "/docs", "/scalar"):
            continue

        method_lower = r["method"].lower()
        param_specs: list[CompiledParamSpec] = r["param_specs"]
        response_model: Type[Schema] | None = r["response_model"]
        status_code = str(r["status_code"])
        tags: list[str] = r["tags"]

        parameters: list[dict[str, Any]] = []
        request_body: dict[str, Any] | None = None

        for p in param_specs:
            if p.source in _SOURCE_IN_MAP:
                param_schema: dict[str, Any] = {
                    "type": _OPENAPI_TYPE_MAP.get(p.param_type, "string")
                }
                if p.has_ge:
                    param_schema["minimum"] = p.int_ge if p.param_type == 1 else p.float_ge
                if p.has_le:
                    param_schema["maximum"] = p.int_le if p.param_type == 1 else p.float_le
                if p.has_gt:
                    param_schema["exclusiveMinimum"] = p.int_gt if p.param_type == 1 else p.float_gt
                if p.has_lt:
                    param_schema["exclusiveMaximum"] = p.int_lt if p.param_type == 1 else p.float_lt

                parameters.append(
                    {
                        "name": p.lookup_key.decode("utf-8"),
                        "in": _SOURCE_IN_MAP[p.source],
                        "required": bool(p.required),
                        "schema": param_schema,
                    }
                )
            elif p.source == 6 and p.schema_spec is not None:  # SRC_BODY_SCHEMA
                model_name = p.schema_spec.schema_cls.__name__
                components_schemas[model_name] = _schema_spec_to_json_schema(p.schema_spec)
                request_body = {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {"$ref": f"#/components/schemas/{model_name}"}
                        }
                    },
                }

        resp_entry: dict[str, Any] = {"description": "Successful Response"}
        rm_spec = get_schema_spec(response_model) if response_model is not None else None
        if rm_spec is not None and response_model is not None:
            rm_name = getattr(response_model, "__name__", "ResponseModel")
            components_schemas[rm_name] = _schema_spec_to_json_schema(rm_spec)
            resp_entry["content"] = {
                "application/json": {
                    "schema": {"$ref": f"#/components/schemas/{rm_name}"}
                }
            }

        operation: dict[str, Any] = {
            "summary": r["endpoint"].__name__.replace("_", " ").title(),
            "operationId": f"{method_lower}_{r['endpoint'].__name__}",
            "responses": {status_code: resp_entry},
        }
        if tags:
            operation["tags"] = tags
        if parameters:
            operation["parameters"] = parameters
        if request_body is not None:
            operation["requestBody"] = request_body

        paths.setdefault(path_str, {})[method_lower] = operation

    spec: dict[str, Any] = {
        "openapi": "3.1.0",
        "info": {"title": title, "version": version},
        "paths": paths,
    }
    if components_schemas:
        spec["components"] = {"schemas": components_schemas}
    return spec


def get_swagger_ui_html(title: str, openapi_url: str = "/openapi.json") -> bytes:
    safe_title = html.escape(title, quote=True)
    safe_url_js = json.dumps(openapi_url)
    doc = f"""<!DOCTYPE html>
<html>
<head>
  <title>{safe_title} - Swagger UI</title>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css">
</head>
<body>
  <div id="swagger-ui"></div>
  <script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
  <script>
    SwaggerUIBundle({{ url: {safe_url_js}, dom_id: '#swagger-ui' }});
  </script>
</body>
</html>"""
    return doc.encode("utf-8")


def get_scalar_ui_html(title: str, openapi_url: str = "/openapi.json") -> bytes:
    safe_title = html.escape(title, quote=True)
    safe_url_attr = html.escape(openapi_url, quote=True)
    doc = f"""<!DOCTYPE html>
<html>
<head>
  <title>{safe_title} - Scalar API Reference</title>
  <meta charset="utf-8" />
</head>
<body>
  <script id="api-reference" data-url="{safe_url_attr}"></script>
  <script src="https://cdn.jsdelivr.net/npm/@scalar/api-reference"></script>
</body>
</html>"""
    return doc.encode("utf-8")
