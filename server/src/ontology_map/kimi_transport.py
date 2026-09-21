"""OpenAI strict JSON transport (legacy module name) shared by all LLM operations.

Preparation finishes before reservation. Each operation sends once, without
redirects or retries. Callers own product validation and durable lifecycle.
"""

import json
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from time import monotonic
from typing import TYPE_CHECKING, Any

import httpx
from pydantic import SecretStr, ValidationError

from ontology_map.kimi_response_archive import ResponseArchive
from ontology_map.llm_config import (
    BASE_URL,
    DEFAULT_TIMEOUT_SECONDS,
    PROVIDER,
    RESERVATION_REQUEST_BYTE_CEILING,
    SCHEMA_ROLES,
    json_messages,
    request_options,
    role_model,
)
from ontology_map.llm_contracts import (
    CallFailed,
    CallLimits,
    check_logging,
    validate_base_url,
)
from ontology_map.llm_diagnostics import (
    carry_failure,
    record_failure,
    record_response_metadata,
    record_validation,
)
from ontology_map.llm_pacing import (
    mark_send,
    provider_turn,
    require_pacer,
)
from ontology_map.llm_response_metadata import (
    envelope_metadata,
    rate_limit_kind,
    safe_headers,
)
from ontology_map.openai_schema import wire_schema

if TYPE_CHECKING:
    from ontology_map.pilot_budget import PilotBudget


def _integer(value: object) -> int | None:
    return value if type(value) is int else None


def _unknown_response(stage: str, reason: str) -> CallFailed:
    error = CallFailed("RESPONSE_UNKNOWN", fatal=True)
    record_failure(error, stage=stage, reason=reason)
    return error


def _checked_usage(payload: dict[str, Any], limits: CallLimits) -> tuple[int, int]:
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        raise _unknown_response("USAGE", "USAGE_MISSING")
    input_tokens = _integer(usage.get("prompt_tokens"))
    output_tokens = _integer(usage.get("completion_tokens"))
    total_tokens = _integer(usage.get("total_tokens"))
    if input_tokens is None or output_tokens is None or total_tokens is None:
        raise _unknown_response("USAGE", "USAGE_TYPE")
    if total_tokens != input_tokens + output_tokens:
        raise _unknown_response("USAGE", "USAGE_TOTAL")
    if not 0 < input_tokens <= limits.max_input_tokens:
        raise _unknown_response("USAGE", "INPUT_LIMIT")
    if not 0 <= output_tokens <= limits.max_output_tokens:
        raise _unknown_response("USAGE", "OUTPUT_LIMIT")
    _checked_details(usage, input_tokens, output_tokens)
    return input_tokens, output_tokens


def _checked_details(usage: dict[str, Any], inputs: int, outputs: int) -> None:
    for group, keys, upper in (
        ("prompt_tokens_details", ("cached_tokens", "cache_write_tokens"), inputs),
        ("completion_tokens_details", ("reasoning_tokens",), outputs),
    ):
        detail = usage.get(group)
        if detail is None:
            continue
        if not isinstance(detail, dict):
            raise _unknown_response("USAGE", "USAGE_DETAIL")
        seen = []
        for key in keys:
            value = detail.get(key)
            if value is not None:
                if type(value) is not int or not 0 <= value <= upper:
                    raise _unknown_response("USAGE", "USAGE_DETAIL")
                seen.append(value)
        if sum(seen) > upper:
            raise _unknown_response("USAGE", "USAGE_DETAIL")


def _output_error(reason: str) -> CallFailed:
    error = CallFailed("OUTPUT_CONTRACT_ERROR", fatal=False)
    record_failure(error, stage="OUTPUT", reason=reason)
    return error


def _checked_content(payload: dict[str, Any]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or len(choices) != 1:
        raise _output_error("CHOICES_SHAPE")
    choice = choices[0]
    if not isinstance(choice, dict) or choice.get("finish_reason") != "stop":
        raise _output_error("FINISH_REASON")
    message = choice.get("message")
    if not isinstance(message, dict):
        raise _output_error("MESSAGE_SHAPE")
    if message.get("refusal") is not None:
        raise _output_error("REFUSAL")
    content = message.get("content")
    if (
        not isinstance(content, str)
        or message.get("tool_calls")
        or message.get("function_call")
    ):
        raise _output_error("CONTENT_SHAPE")
    return content


@dataclass(repr=False)
class PreparedJsonCall:
    """In-memory one-send operation; usage metadata contains no provider prose."""

    _owner: "KimiStructuredTransport"
    _request: httpx.Request = field(repr=False)
    _model: str
    _limits: CallLimits
    _pilot: "PilotBudget | None" = field(repr=False)
    _reservation: int | None
    request_hash: str
    _schema_name: str
    _schema: dict[str, Any] = field(repr=False)
    _role: str
    archive: ResponseArchive = field(default_factory=ResponseArchive, init=False)
    sent: bool = field(default=False, init=False)
    usage: tuple[int, int] | None = field(default=None, init=False)
    _invoked: bool = field(default=False, init=False)

    def __call__(self) -> str:
        with provider_turn():
            return self._send_once()

    def _send_once(self) -> str:
        if self._invoked:
            raise CallFailed("UNEXPECTED_RETRY", fatal=True)
        self._invoked = True
        started_at = monotonic()
        confirmed = False
        stage = "PILOT_ACTIVE"
        http_status = None

        def confirm(input_tokens: int, output_tokens: int) -> None:
            nonlocal confirmed, stage
            stage = "LEDGER_CONFIRM"
            if self._pilot is not None and self._reservation is not None:
                self._pilot.confirm(
                    self._reservation,
                    self._model,
                    input_tokens,
                    output_tokens,
                    elapsed_seconds=monotonic() - started_at,
                )
            self.usage = (input_tokens, output_tokens)
            confirmed = True
            stage = "OUTPUT"

        try:
            if self._pilot is not None:
                self._pilot.require_active()
            stage = "HTTP_SEND"
            require_pacer(live=self._owner._pilot_required)
            mark_send()
            self.sent = True
            response = self._owner._client.send(self._request)
            self.archive.capture(
                response.content,
                role=self._role,
                provider=PROVIDER,
                model=self._model,
                headers=safe_headers(response.headers),
                schema_name=self._schema_name,
                request_hash=self.request_hash,
                http_status=response.status_code,
                pilot_path=self._pilot.path if self._pilot is not None else None,
                pilot_sequence=self._reservation,
            )
            http_status = response.status_code
            stage = "HTTP_STATUS"
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as error:
                record_response_metadata(
                    error, safe_headers(response.headers), rate_limit_kind(response)
                )
                raise
            stage = "RESPONSE_JSON"
            return self._owner._parse(
                response,
                model=self._model,
                limits=self._limits,
                on_usage=confirm,
                archive=self.archive,
            )
        except BaseException as error:
            record_failure(
                error,
                stage=stage,
                request_hash=self.request_hash,
                send_entered=self.sent,
                http_status=http_status,
                usage_confirmed=confirmed,
            )
            carry_failure(error, error, role=self._role)
            self.archive.failed(error)
            if self._pilot is not None and not confirmed:
                self._pilot.stop(error)
            raise

    def parse[T](self, parser: Callable[[str], T]) -> T:
        """Run the caller's unchanged product parser and retain safe field paths."""
        content = self()
        try:
            return parser(content)
        except Exception as error:
            try:
                if isinstance(error, ValidationError):
                    record_validation(error, self._schema)
                record_failure(
                    error,
                    stage="OUTPUT_SCHEMA",
                    request_hash=self.request_hash,
                    send_entered=self.sent,
                    usage_confirmed=self.usage is not None,
                    http_status=self.archive.http_status,
                )
                carry_failure(error, error, role=self._role)
                self.archive.failed(error)
            except Exception:
                pass  # Diagnostic failure must not replace the original exception.
            raise


class KimiStructuredTransport:
    """Only the configured OpenAI role/model pairs and endpoint are allowed."""

    def __init__(
        self,
        api_key: SecretStr,
        *,
        base_url: str = BASE_URL,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        read_timeout_seconds: float | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._base_url = validate_base_url(base_url)
        key = api_key.get_secret_value()
        if not key or any(character.isspace() for character in key):
            raise CallFailed("AUTHENTICATION_ERROR", fatal=True)
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise CallFailed("INVALID_REQUEST", fatal=True)
        if read_timeout_seconds is not None and (
            not math.isfinite(read_timeout_seconds) or read_timeout_seconds <= 0
        ):
            raise CallFailed("INVALID_REQUEST", fatal=True)
        self._endpoint = self._base_url + "/chat/completions"
        self._authorization = "Bearer " + key
        self._pilot_required = not isinstance(transport, httpx.MockTransport)
        self._client = httpx.Client(
            transport=transport or httpx.HTTPTransport(retries=0),
            trust_env=False,
            follow_redirects=False,
            timeout=httpx.Timeout(
                timeout_seconds,
                read=read_timeout_seconds or timeout_seconds,
            ),
        )

    @property
    def endpoint(self) -> str:
        return self._endpoint

    def close(self) -> None:
        self._client.close()

    def prepare(
        self,
        *,
        model: str,
        messages: Sequence[dict[str, str]],
        schema_name: str,
        schema: dict[str, Any],
        limits: CallLimits,
    ) -> PreparedJsonCall:
        check_logging()
        role = SCHEMA_ROLES.get(schema_name)
        if role is None or model != role_model(role):
            raise CallFailed("INVALID_REQUEST", fatal=True)
        try:
            CallLimits(
                limits.max_input_tokens,
                limits.max_output_tokens,
                limits.max_request_bytes,
            )
            body = {
                "model": model,
                "messages": json_messages(messages, schema_name, schema),
                "max_completion_tokens": limits.max_output_tokens,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": schema_name,
                        "strict": True,
                        "schema": wire_schema(schema),
                    },
                },
                **request_options(role),
            }
            content = json.dumps(
                body, ensure_ascii=False, separators=(",", ":"), allow_nan=False
            ).encode("utf-8")
        except TypeError, ValueError, OverflowError:
            raise CallFailed("INVALID_REQUEST", fatal=True) from None
        if len(content) > min(
            limits.max_request_bytes, RESERVATION_REQUEST_BYTE_CEILING
        ):
            raise CallFailed("REQUEST_SIZE_LIMIT", fatal=True)
        prepared = self._client.build_request(
            "POST",
            self._endpoint,
            headers={
                "Authorization": self._authorization,
                "Content-Type": "application/json",
            },
            content=content,
        )
        # Lazy import avoids the legacy model_studio -> pilot_budget cycle.
        from ontology_map.pilot_budget import current_pilot, request_digest

        digest = request_digest(prepared)
        pilot = current_pilot(required=self._pilot_required)
        try:
            require_pacer(live=self._pilot_required)
            with provider_turn():
                reservation = pilot.reserve(model, limits, digest) if pilot else None
        except Exception as error:
            record_failure(
                error,
                stage="PILOT_RESERVE",
                request_hash=digest,
                send_entered=False,
                usage_confirmed=False,
            )
            raise
        return PreparedJsonCall(
            self,
            prepared,
            model,
            limits,
            pilot,
            reservation,
            digest,
            schema_name,
            schema,
            role,
        )

    @staticmethod
    def _parse(
        response: httpx.Response,
        *,
        model: str,
        limits: CallLimits,
        on_usage: Callable[[int, int], None],
        archive: ResponseArchive | None = None,
    ) -> str:
        try:
            payload = response.json()
        except ValueError:
            raise _unknown_response("RESPONSE_JSON", "JSON_INVALID") from None
        if not isinstance(payload, dict):
            raise _unknown_response("RESPONSE_JSON", "ENVELOPE_NOT_OBJECT")
        if archive is not None:
            archive.annotate(envelope_metadata(payload))
        if payload.get("model") != model:
            raise _unknown_response("RESPONSE_MODEL", "MODEL_MISMATCH")
        on_usage(*_checked_usage(payload, limits))
        return _checked_content(payload)


OpenAIStructuredTransport = KimiStructuredTransport
