"""Result-affecting execution settings for #68 NODE_INSIGHT.

These settings are part of the durable effective-input identity and are also
consumed by the production provider adapter. Credentials, worker identity and
other infrastructure-only values never belong here.
"""

from ontology_map.model_studio import MAX_INPUT_TOKENS, CallLimits
from ontology_map.structured_provider import request_identity_settings

INSIGHT_LIMITS = CallLimits(
    max_input_tokens=MAX_INPUT_TOKENS,
    max_output_tokens=8_192,
    max_request_bytes=8 * 1024 * 1024,
)


def identity_settings() -> dict[str, object]:
    """Return deterministic settings that can change provider output/acceptance."""
    limits = INSIGHT_LIMITS
    return {
        "limits": {
            "max_input_tokens": limits.max_input_tokens,
            "max_output_tokens": limits.max_output_tokens,
            "max_request_bytes": limits.max_request_bytes,
        },
        "structured_request": request_identity_settings("insight"),
    }
