"""Lossless constraint translation for the supported strict-output subset.

The product DTO/schema is never edited. Optional fields become mandatory on the
wire using their EXISTING type (nullable only when the product says nullable).
Defaults are annotations, not constraints. Unknown semantic keywords fail closed.
Actual API acceptance still requires a separately approved live contract check.
"""

from copy import deepcopy
from typing import Any

_ALLOWED = frozenset(
    {
        "$defs",
        "$ref",
        "type",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "anyOf",
        "enum",
        "const",
        "default",
        "title",
        "description",
        "pattern",
        "format",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "minItems",
        "maxItems",
        "minLength",
        "maxLength",
    }
)
_FORMATS = frozenset(
    {
        "date-time",
        "time",
        "date",
        "duration",
        "email",
        "hostname",
        "ipv4",
        "ipv6",
        "uuid",
    }
)
_PYDANTIC_DECIMAL_PATTERN = r"^(?!^[-+.]*$)[+-]?0*\d*\.?\d*$"
_WIRE_DECIMAL_PATTERN = r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$"


def _object(node: dict[str, Any]) -> None:
    properties = node.get("properties")
    if (
        not isinstance(properties, dict)
        or node.get("additionalProperties") is not False
    ):
        raise ValueError("UNSUPPORTED_OPEN_OBJECT")
    required = node.get("required", [])
    if not isinstance(required, list) or set(required) - properties.keys():
        raise ValueError("INVALID_SCHEMA_REQUIRED")
    node["required"] = sorted(properties)
    for value in properties.values():
        _visit(value)


def _visit(node: Any) -> None:
    if not isinstance(node, dict) or set(node) - _ALLOWED:
        raise ValueError("UNSUPPORTED_WIRE_SCHEMA")
    node.pop("default", None)
    # OpenAI rejects Pydantic's lookahead; this equivalent regex keeps the
    # string constraint, while the original Decimal parser stays authoritative.
    if node.get("pattern") == _PYDANTIC_DECIMAL_PATTERN:
        node["pattern"] = _WIRE_DECIMAL_PATTERN
    if "const" in node:
        value = node.pop("const")
        if "enum" in node and node["enum"] != [value]:
            raise ValueError("UNSUPPORTED_CONST_ENUM")
        node["enum"] = [value]
    if "format" in node and node["format"] not in _FORMATS:
        raise ValueError("UNSUPPORTED_WIRE_FORMAT")
    if node.get("type") == "object":
        _object(node)
    for value in node.get("$defs", {}).values():
        _visit(value)
    if "items" in node:
        _visit(node["items"])
    for value in node.get("anyOf", []):
        _visit(value)


def wire_schema(product_schema: dict[str, Any]) -> dict[str, Any]:
    if product_schema.get("type") != "object" or "anyOf" in product_schema:
        raise ValueError("INVALID_ROOT_SCHEMA")
    result = deepcopy(product_schema)
    _visit(result)
    # Python's cross-field validator is NOT encoded in ordinary JSON Schema.
    # Guide the model without weakening the authoritative Mention validator.
    mention = result.get("$defs", {}).get("Mention")
    if mention is not None:
        mention["description"] = (
            "topic_name must be non-null exactly when node_type is TOPIC; "
            "otherwise topic_name must be null. Use only approved Topic names."
        )
    return result
