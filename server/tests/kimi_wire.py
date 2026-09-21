"""Assertions on actual mock HTTP bytes for OpenAI strict Structured Outputs."""

import json

from ontology_map.llm_config import (
    OUTPUT_INSTRUCTION,
    SCHEMA_ROLES,
    SCHEMA_SEPARATOR,
    request_options,
    role_model,
)
from ontology_map.openai_schema import wire_schema as compile_wire_schema


def wire_schema(payload):
    fmt = payload["response_format"]
    assert fmt["type"] == "json_schema"
    contract = fmt["json_schema"]
    assert contract["strict"] is True
    role = SCHEMA_ROLES[contract["name"]]
    assert payload["model"] == role_model(role)
    for key, value in request_options(role).items():
        assert payload[key] == value
    assert "max_completion_tokens" in payload
    assert (
        not {
            "temperature",
            "top_p",
            "thinking",
            "enable_thinking",
            "tools",
            "max_tokens",
        }
        & payload.keys()
    )
    content = payload["messages"][0]["content"]
    prefix, encoded = content.rsplit(SCHEMA_SEPARATOR, 1)
    assert prefix.endswith(OUTPUT_INSTRUCTION)
    original = json.loads(encoded)
    assert contract["schema"] == compile_wire_schema(original)
    return original


def task_prompt(payload):
    content = payload["messages"][0]["content"]
    return content.rsplit("\n\n" + OUTPUT_INSTRUCTION + SCHEMA_SEPARATOR, 1)[0]
