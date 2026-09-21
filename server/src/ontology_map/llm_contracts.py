"""Provider-call limits and safe errors; no transmission or persistence."""

import logging
import sys
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

from ontology_map.llm_config import (
    BASE_URL,
    LUNA_MODEL,
    MAX_INPUT_TOKENS,
    MAX_OUTPUT_TOKENS,
    MODEL_VERSION,
)

Role = Literal[
    "generation",
    "claim_review",
    "entity_resolution",
    "node_context",
    "followup",
    "insight",
]
# Standard short-context USD / million, official docs checked 2026-09-18.
# Historical Kimi pricing remains available for reading old records, not sends.
RATES = {
    MODEL_VERSION: (Decimal("2.00"), Decimal("12.00")),
    LUNA_MODEL: (Decimal("0.20"), Decimal("1.20")),
    "kimi-k2.6": (Decimal("0.95"), Decimal("4.00")),
}


@dataclass(frozen=True)
class CallLimits:
    max_input_tokens: int
    max_output_tokens: int
    max_request_bytes: int

    def __post_init__(self) -> None:
        if (
            type(self.max_input_tokens) is not int
            or not 0 < self.max_input_tokens <= MAX_INPUT_TOKENS
        ):
            raise ValueError("INVALID_INPUT_LIMIT")
        if (
            type(self.max_output_tokens) is not int
            or not 0 < self.max_output_tokens <= MAX_OUTPUT_TOKENS
            or type(self.max_request_bytes) is not int
            or self.max_request_bytes <= 0
        ):
            raise ValueError("INVALID_REQUEST_LIMIT")


@dataclass(frozen=True)
class CallRecord:
    role: Role
    model: str
    request_hash: str
    status: str
    input_tokens: int | None
    output_tokens: int | None
    reserved_usd: Decimal
    charged_upper_usd: Decimal
    elapsed_seconds: float


@dataclass
class Budget:
    max_calls: int | None
    max_usd: Decimal | None
    demo_uncapped: bool = False
    charged_upper_usd: Decimal = field(default=Decimal(0), init=False)
    records: list[CallRecord] = field(default_factory=list, init=False)
    stopped: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        if (
            type(self.demo_uncapped) is not bool
            or (
                self.demo_uncapped
                and (self.max_calls is not None or self.max_usd is not None)
            )
            or (
                not self.demo_uncapped
                and (
                    type(self.max_calls) is not int
                    or self.max_calls < 0
                    or not isinstance(self.max_usd, Decimal)
                    or not self.max_usd.is_finite()
                    or self.max_usd < 0
                )
            )
        ):
            raise ValueError("INVALID_BUDGET")

    def reserve(self, amount: Decimal) -> None:
        if self.stopped or (
            not self.demo_uncapped and len(self.records) >= self.max_calls  # type: ignore[operator]
        ):
            self.stopped = True
            raise CallFailed("CALL_LIMIT", fatal=True)
        if (
            not self.demo_uncapped and self.charged_upper_usd + amount > self.max_usd  # type: ignore[operator]
        ):
            self.stopped = True
            raise CallFailed("COST_LIMIT", fatal=True)
        self.charged_upper_usd += amount


class CallFailed(Exception):
    """Only an allowlisted code escapes; never use provider exception text."""

    def __init__(self, code: str, *, fatal: bool) -> None:
        super().__init__(code)
        self.code = code
        self.fatal = fatal


def validate_base_url(base_url: str) -> str:
    if base_url != BASE_URL:
        raise CallFailed("UNAPPROVED_ENDPOINT", fatal=True)
    return base_url


def token_cost(model: str, input_tokens: int, output_tokens: int) -> Decimal:
    input_rate, output_rate = RATES[model]
    # Worst case: every input token is a cache write (1.25x). No cache discount
    # is assumed. completion_tokens already INCLUDE reasoning tokens. This is
    # charged_upper_usd, not an assertion of actual provider billing.
    if model in {MODEL_VERSION, LUNA_MODEL}:
        input_rate *= Decimal("1.25")
    return (input_rate * input_tokens + output_rate * output_tokens) / 1_000_000


def check_logging() -> None:
    # No LangChain callbacks/tracing are used by this HTTP transport. Still reject
    # an already-enabled legacy global debug/verbose configuration.
    globals_module = sys.modules.get("langchain_core.globals")
    if globals_module is not None and (
        globals_module.get_debug() or globals_module.get_verbose()
    ):
        raise CallFailed("UNSAFE_LOGGING_CONFIGURATION", fatal=True)
    for name in ("openai", "httpx", "httpcore", "langchain_core", "langsmith"):
        if logging.getLogger(name).isEnabledFor(logging.DEBUG):
            raise CallFailed("UNSAFE_LOGGING_CONFIGURATION", fatal=True)
