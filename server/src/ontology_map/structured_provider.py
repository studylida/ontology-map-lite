"""Compatibility names for the shared OpenAI strict-output transport.

The old names do not enable Kimi/Alibaba or fallback. A compatible strict wire
schema guides generation; original product validators remain authoritative.
"""

from ontology_map.kimi_transport import KimiStructuredTransport
from ontology_map.llm_config import DEFAULT_TIMEOUT_SECONDS, request_identity_settings

ModelStudioStructuredTransport = KimiStructuredTransport

# Kept for consumers inspecting the old constants. None means omitted on wire.
REQUEST_TEMPERATURE = None
REQUEST_STREAM = False
REQUEST_ENABLE_THINKING = False
REQUEST_RESPONSE_FORMAT = "json_schema"
REQUEST_SCHEMA_STRICT = True

__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "KimiStructuredTransport",
    "ModelStudioStructuredTransport",
    "request_identity_settings",
    "REQUEST_TEMPERATURE",
    "REQUEST_STREAM",
    "REQUEST_ENABLE_THINKING",
    "REQUEST_RESPONSE_FORMAT",
    "REQUEST_SCHEMA_STRICT",
]
