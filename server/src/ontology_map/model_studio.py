"""Compatibility imports for the former Model Studio boundary.

Every live call now uses OpenAI. ModelStudio, FLASH and PLUS remain
source-compatible aliases, not alternate providers or models. Product schemas,
role semantics and durable lifecycle are unchanged.
"""

from time import monotonic
from typing import get_args

import httpx
from pydantic import BaseModel, SecretStr, ValidationError

from ontology_map.llm_config import (
    BASE_URL,
    BILLABLE_INPUT_CEILING,
    MAX_INPUT_TOKENS,
    MODEL_VERSION,
    SCHEMA_ROLES,
    role_model,
)
from ontology_map.llm_contracts import (
    RATES,
    Budget,
    CallFailed,
    CallLimits,
    CallRecord,
    Role,
    check_logging,
    token_cost,
    validate_base_url,
)
from ontology_map.llm_pacing import provider_turn

# Compatibility: generation remains FLASH/Terra; helpers select by ROLE_PROFILES.
FLASH = PLUS = MODEL_VERSION

__all__ = [
    "BASE_URL",
    "FLASH",
    "PLUS",
    "MAX_INPUT_TOKENS",
    "RATES",
    "Budget",
    "CallFailed",
    "CallLimits",
    "CallRecord",
    "Role",
    "token_cost",
    "validate_base_url",
    "KimiModels",
    "ModelStudio",
]


class KimiModels:
    """One sequential helper execution; validated objects only leave this API."""

    def __init__(
        self,
        api_key: SecretStr,
        budget: Budget,
        *,
        base_url: str = BASE_URL,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        from ontology_map.kimi_transport import KimiStructuredTransport

        self.budget = budget
        self._transport = KimiStructuredTransport(
            api_key, base_url=base_url, transport=transport
        )

    def close(self) -> None:
        self._transport.close()

    def call[T: BaseModel](
        self,
        role: Role,
        prompt: str,
        payload: BaseModel,
        schema: type[T],
        limits: CallLimits,
    ) -> T:
        with provider_turn():
            return self._call(role, prompt, payload, schema, limits)

    def _call[T: BaseModel](
        self,
        role: Role,
        prompt: str,
        payload: BaseModel,
        schema: type[T],
        limits: CallLimits,
    ) -> T:
        if role not in get_args(Role):
            raise CallFailed("UNKNOWN_ROLE", fatal=True)
        check_logging()
        if SCHEMA_ROLES.get(schema.__name__) != role:
            raise CallFailed("INVALID_REQUEST", fatal=True)
        from ontology_map.pilot_budget import PilotBudgetError

        model = role_model(role)
        reserved = token_cost(model, BILLABLE_INPUT_CEILING, limits.max_output_tokens)
        self.budget.reserve(reserved)
        operation = None
        started = monotonic()
        status = "RESPONSE_UNKNOWN"
        try:
            operation = self._transport.prepare(
                model=model,
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": payload.model_dump_json()},
                ],
                schema_name=schema.__name__,
                schema=schema.model_json_schema(),
                limits=limits,
            )
            result = operation.parse(
                lambda content: schema.model_validate_json(content, strict=True)
            )
            status = "SUCCESS"
            return result
        except ValidationError:
            status = "OUTPUT_CONTRACT_ERROR"
            raise CallFailed(status, fatal=False) from None
        except CallFailed as error:
            status = error.code
            local_preflight = operation is None and error.code in {
                "REQUEST_SIZE_LIMIT",
                "INVALID_REQUEST",
            }
            self.budget.stopped = error.fatal and not local_preflight
            raise
        except PilotBudgetError:
            self.budget.stopped = True
            raise
        except Exception:
            self.budget.stopped = True
            # The durable adapter preserves typed HTTP errors for its classifier.
            # Runtime helpers retain their existing safe-code-only error API.
            raise CallFailed(status, fatal=True) from None
        finally:
            if operation is not None and operation.sent:
                usage = operation.usage
                input_tokens, output_tokens = usage if usage else (None, None)
                charged = reserved if usage is None else token_cost(model, *usage)
                self.budget.charged_upper_usd += charged - reserved
                self.budget.records.append(
                    CallRecord(
                        role,
                        model,
                        operation.request_hash,
                        status,
                        input_tokens,
                        output_tokens,
                        reserved,
                        charged,
                        monotonic() - started,
                    )
                )
            else:
                self.budget.charged_upper_usd -= reserved


ModelStudio = KimiModels
