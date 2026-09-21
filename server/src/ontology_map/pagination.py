import base64
import binascii
import json


class InvalidCursorError(ValueError):
    pass


def encode_cursor(
    kind: str,
    scope: dict[str, int | str],
    values: list[int | str | None],
) -> str:
    payload = json.dumps(
        {"v": 1, "kind": kind, "scope": scope, "values": values},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return base64.urlsafe_b64encode(payload).rstrip(b"=").decode()


def decode_cursor(
    cursor: str,
    *,
    kind: str,
    scope: dict[str, int | str],
) -> list[object]:
    try:
        padding = "=" * (-len(cursor) % 4)
        raw = base64.b64decode(cursor + padding, altchars=b"-_", validate=True)
        payload = json.loads(raw)
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise InvalidCursorError from error

    if (
        type(payload) is not dict
        or set(payload) != {"v", "kind", "scope", "values"}
        or type(payload["v"]) is not int
        or payload["v"] != 1
        or payload["kind"] != kind
        or payload["scope"] != scope
        or type(payload["values"]) is not list
    ):
        raise InvalidCursorError
    return payload["values"]
