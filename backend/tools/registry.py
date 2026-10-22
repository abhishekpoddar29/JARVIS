import inspect
import re
import types
from enum import Enum
from typing import Any, Literal, Union, get_args, get_origin, get_type_hints



_FUNCTION_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")


def _schema_for(annotation: Any) -> dict[str, Any]:
    if annotation is inspect.Signature.empty or annotation is Any:
        return {"type": "string"}

    origin = get_origin(annotation)
    args = get_args(annotation)

    if origin in (Union, types.UnionType):
        non_null = [arg for arg in args if arg is not type(None)]
        return _schema_for(non_null[0]) if non_null else {"type": "string"}
    if origin is Literal:
        values = list(args)
        schema = _schema_for(type(values[0])) if values else {"type": "string"}
        schema["enum"] = values
        return schema
    if origin in (list, tuple, set):
        return {"type": "array", "items": _schema_for(args[0] if args else Any)}
    if origin is dict:
        return {"type": "object"}

    if inspect.isclass(annotation) and issubclass(annotation, Enum):
        return {"type": "string", "enum": [member.value for member in annotation]}

    return {
        str: {"type": "string"},
        int: {"type": "integer"},
        float: {"type": "number"},
        bool: {"type": "boolean"},
        dict: {"type": "object"},
        list: {"type": "array", "items": {}},
    }.get(annotation, {"type": "string"})


def _parameters_for(tool: Any) -> dict[str, Any]:
    args_schema = getattr(tool, "args_schema", None)
    if args_schema and hasattr(args_schema, "model_json_schema"):
        raw = args_schema.model_json_schema()
        return {
            "type": "object",
            "properties": raw.get("properties", {}),
            "required": raw.get("required", []),
        }

    run = getattr(tool, "run", None) or getattr(tool, "arun", None)
    if not callable(run):
        return {"type": "object", "properties": {}}

    signature = inspect.signature(run)
    try:
        hints = get_type_hints(run)
    except Exception:
        hints = {}

    properties: dict[str, Any] = {}
    required: list[str] = []

    for name, parameter in signature.parameters.items():
        if name == "self" or parameter.kind in {
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        }:
            continue

        properties[name] = _schema_for(hints.get(name, parameter.annotation))
        if parameter.default is inspect.Signature.empty:
            required.append(name)

    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Any] = {}

    def register(self, tool: Any) -> None:
        self._tools[tool.name] = tool

    def list_tools(self) -> list[Any]:
        return list(self._tools.values())

    def get(self, name: str) -> Any | None:
        return self._tools.get(name)

    def exists(self, name: str) -> bool:
        return name in self._tools

    def function_declarations(self) -> list[dict[str, Any]]:
        declarations: list[dict[str, Any]] = []
        for tool in self.list_tools():
            name = str(getattr(tool, "name", "")).strip()
            if not _FUNCTION_NAME.fullmatch(name):
                continue
            declarations.append({
                "name": name,
                "description": str(getattr(tool, "description", ""))[:1024],
                "parameters": _parameters_for(tool),
            })
        return declarations


registry = ToolRegistry()