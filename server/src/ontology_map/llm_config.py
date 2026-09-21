"""Versioned OpenAI role profiles for every product LLM role.

No environment reads or network/DB IO at import time. The same values build the
wire request and effective-input identity. There is no alternate-provider path.
"""

import json
from collections.abc import Mapping, Sequence
from hashlib import sha256
from types import MappingProxyType
from typing import Any

PROVIDER = "openai"
BASE_URL = "https://api.openai.com/v1"
MODEL_VERSION = "gpt-5.6-terra"
LUNA_MODEL = "gpt-5.6-luna"
PROFILE_VERSION = "openai-strict-roles-v1"
WIRE_SCHEMA_VERSION = "openai-product-schema-v2"
# Existing application caps; do not expand to the provider context window.
# Keep a full 32K output allowance even when a particular call uses less.
CONTEXT_CAP_TOKENS = 256_000
MAX_OUTPUT_TOKENS = 32_768
MAX_INPUT_TOKENS = CONTEXT_CAP_TOKENS - MAX_OUTPUT_TOKENS
# Conservative legacy input reservation; below OpenAI long-context surcharge.
# This is not a tokenizer estimate or a new model-context admission limit.
BILLABLE_INPUT_CEILING = 262_144
# Provider window is larger than the old reservation. A UTF-8 byte cannot encode
# more than one token, so this byte ceiling stays below the application input cap.
# This is an admission guard, not exact tokenization or a billing claim.
INPUT_BYTE_FACTOR = 1
INPUT_FRAMING_ALLOWANCE = 4096
RESERVATION_REQUEST_BYTE_CEILING = (
    MAX_INPUT_TOKENS - INPUT_FRAMING_ALLOWANCE
) // INPUT_BYTE_FACTOR
DEFAULT_TIMEOUT_SECONDS = 60.0
GENERATION_READ_TIMEOUT_SECONDS = 180.0
SCHEMA_SEPARATOR = "\n\nOUTPUT_SCHEMA_JSON:\n"
OUTPUT_INSTRUCTION = (
    "Return exactly one JSON object matching the output contract below. "
    "Return every required field, including required nullable fields with JSON null. "
    "Do not add keys, commentary, Markdown fences, or reasoning. "
    "The contract is a schema, not an output example; return data, not the schema. "
    "Preserve all source references and the task's semantic constraints."
)


# Static, unvalidated-quality starting deployment; never a self-routing policy.
ROLE_PROFILES = MappingProxyType(
    {
        "node_context": (LUNA_MODEL, "low"),
        "followup": (LUNA_MODEL, "low"),
        "generation": (MODEL_VERSION, "medium"),
        "claim_review": (MODEL_VERSION, "medium"),
        "entity_resolution": (MODEL_VERSION, "medium"),
        "insight": (MODEL_VERSION, "medium"),
    }
)
SCHEMA_ROLES = MappingProxyType(
    {
        "KnowledgeProposals": "generation",
        "ClaimReviewBatch": "claim_review",
        "_TerraSupportBatch": "claim_review",
        "ResolutionBatchProposal": "entity_resolution",
        "NodeContextProposal": "node_context",
        "FollowupQuestionsProposal": "followup",
        "InsightBundleProposal": "insight",
    }
)


def role_model(role: str) -> str:
    return ROLE_PROFILES[role][0]


def request_options(role: str = "generation") -> dict[str, Any]:
    """Shared options only; the strict output schema is attached separately."""
    return {
        "stream": False,
        "store": False,
        "reasoning_effort": ROLE_PROFILES[role][1],
    }


def request_identity_settings(role: str | None = None) -> dict[str, Any]:
    """Profiles, schema/prompt version and caps travel with effective input.

    No credentials, pacing configuration or mutable account limits enter task
    identity. Model response aliases are exact, not guessed snapshot prefixes.
    """
    roles = (role,) if role is not None else tuple(ROLE_PROFILES)
    return {
        "provider": PROVIDER,
        "base_url": BASE_URL,
        "endpoint": BASE_URL + "/chat/completions",
        "model": role_model(role or "generation"),
        "profile_version": PROFILE_VERSION,
        "wire_schema_version": WIRE_SCHEMA_VERSION,
        "role_profiles": {
            key: {"model": role_model(key), **request_options(key)} for key in roles
        },
        "output_option": "max_completion_tokens",
        "response_format": "json_schema",
        "schema_strict": True,
        "local_schema_strict": True,
        "output_instruction_sha256": sha256(OUTPUT_INSTRUCTION.encode()).hexdigest(),
        "schema_separator": SCHEMA_SEPARATOR,
        "context_cap_tokens": CONTEXT_CAP_TOKENS,
        "max_input_tokens": MAX_INPUT_TOKENS,
        "max_output_tokens": MAX_OUTPUT_TOKENS,
        "reservation_admission": {
            "input_byte_factor": INPUT_BYTE_FACTOR,
            "framing_allowance": INPUT_FRAMING_ALLOWANCE,
            "max_request_bytes": RESERVATION_REQUEST_BYTE_CEILING,
        },
    }


def json_messages(
    messages: Sequence[Mapping[str, str]],
    schema_name: str,
    schema: dict[str, Any],
) -> list[dict[str, str]]:
    """Attach the unchanged product schema as prompt guidance.

    Keep the user's payload as a separate message. Schema keywords such as
    pattern/format/$ref are preserved for local validation and model guidance;
    a separate compatible strict wire schema is compiled without changing it.
    """
    if not schema_name.strip() or schema.get("type") != "object" or not messages:
        raise ValueError("INVALID_OUTPUT_CONTRACT")
    result: list[dict[str, str]] = []
    for message in messages:
        if set(message) != {"role", "content"}:
            raise ValueError("INVALID_MESSAGE")
        role, content = message["role"], message["content"]
        if (
            role not in {"system", "user"}
            or not isinstance(content, str)
            or not content.strip()
        ):
            raise ValueError("INVALID_MESSAGE")
        result.append({"role": role, "content": content})
    contract = json.dumps(
        schema,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    suffix = "\n\n" + OUTPUT_INSTRUCTION + SCHEMA_SEPARATOR + contract
    if result[0]["role"] == "system":
        result[0]["content"] += suffix
    else:
        result.insert(
            0,
            {
                "role": "system",
                "content": OUTPUT_INSTRUCTION + SCHEMA_SEPARATOR + contract,
            },
        )
    return result
