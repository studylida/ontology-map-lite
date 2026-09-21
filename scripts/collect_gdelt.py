#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14,<3.15"
# dependencies = [
#   "trafilatura==2.2.0",
# ]
# ///
"""Collect and audit the fixed Issue #131 GDELT corpus outside the repository."""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import ipaddress
import json
import os
import re
import socket
import sys
import tempfile
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import zip_longest
from pathlib import Path
from typing import Any

from trafilatura import bare_extraction

SOURCE_TABLE = "gdelt-bq.gdeltv2.webngrams"
WINDOW_START = "2026-06-11T00:00:00Z"
WINDOW_END = "2026-09-09T00:00:00Z"
INTERVALS = (
    ("2026-06-11T00:00:00Z", "2026-06-26T00:00:00Z"),
    ("2026-06-26T00:00:00Z", "2026-07-11T00:00:00Z"),
    ("2026-07-11T00:00:00Z", "2026-07-26T00:00:00Z"),
    ("2026-07-26T00:00:00Z", "2026-08-10T00:00:00Z"),
    ("2026-08-10T00:00:00Z", "2026-08-25T00:00:00Z"),
    ("2026-08-25T00:00:00Z", "2026-09-09T00:00:00Z"),
)
USER_AGENT = (
    "ontology-map-gdelt-collector/1.0 "
    "(+https://github.com/studylida/ontology-map/issues/131)"
)
SK_ALIASES = ("SK하이닉스", "SK 하이닉스", "SK Hynix")
PARTNER_ALIASES = {
    "samsung": ("삼성전자", "삼성 전자", "Samsung Electronics"),
    "intel": ("인텔", "Intel", "INTEL"),
    "nvidia": ("엔비디아", "NVIDIA", "Nvidia"),
}
PARTNER_FLAGS = {
    "samsung": "has_samsung",
    "intel": "has_intel",
    "nvidia": "has_nvidia",
}
PUBLIC_STATUSES = {
    "SELECTED",
    "DUPLICATE_URL",
    "DUPLICATE_BODY",
    "ELIGIBLE_NOT_SELECTED",
    "EXCLUDED_DATE",
    "EXCLUDED_LANGUAGE",
    "EXCLUDED_RELEVANCE",
    "BLOCKED_ROBOTS",
    "FAILED_HTTP",
    "FAILED_EXTRACTION",
}
PUBLIC_FIELDS = (
    "schema_version",
    "discovery_key",
    "source_table",
    "query_interval_start",
    "query_interval_end",
    "seen_at",
    "original_url",
    "final_url",
    "canonical_url",
    "title",
    "publisher",
    "published_at",
    "published_precision",
    "collected_at",
    "partner_tags",
    "partner_slot",
    "relevance",
    "robots_allowed",
    "http_status",
    "status",
    "failure_reason",
    "duplicate_of",
    "body_sha256",
    "body_bytes",
    "artifact_key",
    "hangul_chars",
    "alphabetic_chars",
    "hangul_ratio",
)
TRACKING_PARAMETERS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
}
AUDIT_SALT = "ontology-map-131-audit-v1"
AUDIT_FIELDS = (
    "discovery_key",
    "query_interval_start",
    "canonical_url",
    "title",
    "publisher",
    "published_at",
    "partner_slot",
    "body_sha256",
    "artifact_key",
    "manual_date_ok",
    "manual_korean_ok",
    "manual_extraction_ok",
    "manual_direct_relevance",
    "manual_partner_valid",
    "notes",
)
TRUE_VALUES = {"1", "true", "yes", "y"}
MAX_HTML_BYTES = 10 * 1024 * 1024
MAX_ROBOTS_BYTES = 1024 * 1024
HTTP_TIMEOUT_SECONDS = 20
DEFAULT_ORIGIN_DELAY_SECONDS = 1.0
FULL_CANDIDATE_FIELDS = {
    "query_interval_start",
    "query_interval_end",
    "seen_at",
    "url",
    "has_sk",
    *PARTNER_FLAGS.values(),
}
SAMPLE_CANDIDATE_FIELDS = {
    "stratum",
    "sample_rank",
    "candidate_count",
    "url",
    "seen_at",
}


@dataclass
class Candidate:
    interval_start: str
    interval_end: str
    seen_at: str
    url: str
    flags: dict[str, bool]
    discovery_key: str
    duplicate_discoveries: list[dict[str, str]] = field(default_factory=list)


@dataclass
class RobotsPolicy:
    parser: urllib.robotparser.RobotFileParser
    delay: float
    allowed: bool
    reason: str = ""


@dataclass
class CollectionState:
    checkpoint: Path
    latest: dict[str, dict[str, Any]]
    retries: Counter[str]
    terminal: set[str]
    canonical_seen: dict[str, str]
    body_seen: dict[str, str]
    partner_counts: Counter[str]


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        validate_public_https_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class RobotsCache:
    def __init__(self) -> None:
        self._policies: dict[str, RobotsPolicy] = {}
        self._last_request: dict[str, float] = {}
        self._opener = urllib.request.build_opener(SafeRedirectHandler())

    def wait(self, origin: str, delay: float) -> None:
        remaining = delay - (time.monotonic() - self._last_request.get(origin, 0.0))
        if remaining > 0:
            time.sleep(remaining)

    def mark_request(self, origin: str) -> None:
        self._last_request[origin] = time.monotonic()

    def policy_for(self, url: str) -> RobotsPolicy:
        origin = url_origin(url)
        if origin in self._policies:
            return self._policies[origin]
        robots_url = f"{origin}/robots.txt"
        parser = urllib.robotparser.RobotFileParser(robots_url)
        try:
            self.wait(origin, DEFAULT_ORIGIN_DELAY_SECONDS)
            response = fetch_bytes(
                self._opener,
                robots_url,
                MAX_ROBOTS_BYTES,
                accepted_content_types=None,
            )
            self.mark_request(origin)
            parser.parse(
                response["body"].decode("utf-8", errors="replace").splitlines()
            )
        except urllib.error.HTTPError as error:
            self.mark_request(origin)
            if error.code in {401, 403}:
                policy = RobotsPolicy(parser, 0.0, False, f"robots_http_{error.code}")
                self._policies[origin] = policy
                return policy
            if error.code != 404:
                policy = RobotsPolicy(parser, 0.0, False, f"robots_http_{error.code}")
                self._policies[origin] = policy
                return policy
            parser.parse([])
        except (OSError, ValueError) as error:
            self.mark_request(origin)
            policy = RobotsPolicy(
                parser, 0.0, False, f"robots_unavailable:{type(error).__name__}"
            )
            self._policies[origin] = policy
            return policy
        delay = robots_delay(parser)
        policy = RobotsPolicy(
            parser,
            delay,
            parser.can_fetch(USER_AGENT, url),
            "" if parser.can_fetch(USER_AGENT, url) else "robots_disallow",
        )
        self._policies[origin] = policy
        return policy

    def fetch(self, url: str, delay: float) -> dict[str, Any]:
        origin = url_origin(url)
        self.wait(origin, delay)
        try:
            return fetch_bytes(
                self._opener,
                url,
                MAX_HTML_BYTES,
                accepted_content_types=("text/html", "application/xhtml+xml"),
            )
        finally:
            self.mark_request(origin)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def parse_bool(value: str) -> bool:
    return value.strip().casefold() in TRUE_VALUES


def parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(
        value.strip().replace(" UTC", "+00:00").replace("Z", "+00:00")
    )
    if parsed.tzinfo is None:
        raise ValueError(f"timezone is required: {value}")
    return parsed.astimezone(UTC)


def format_timestamp(value: str) -> str:
    return parse_timestamp(value).isoformat().replace("+00:00", "Z")


def discovery_key(
    interval_start: str, interval_end: str, seen_at: str, url: str
) -> str:
    raw = json.dumps(
        [SOURCE_TABLE, interval_start, interval_end, seen_at, url],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"gdelt-webngrams:{sha256_text(raw)}"


def ensure_private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)


def write_private_bytes(path: Path, value: bytes) -> None:
    ensure_private_directory(path.parent)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    with temporary.open("wb") as handle:
        os.chmod(handle.fileno(), 0o600)
        handle.write(value)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)
    path.chmod(0o600)


def append_checkpoint(path: Path, record: dict[str, Any]) -> None:
    ensure_private_directory(path.parent)
    line = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        with os.fdopen(descriptor, "a", encoding="utf-8", closefd=False) as handle:
            handle.write(f"{line}\n")
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)
    path.chmod(0o600)


def load_checkpoint(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    raw = path.read_bytes()
    records: list[dict[str, Any]] = []
    offset = 0
    lines = raw.splitlines(keepends=True)
    for index, line in enumerate(lines):
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as error:
            if index != len(lines) - 1:
                raise ValueError(f"malformed checkpoint line {index + 1}") from error
            with path.open("r+b") as handle:
                handle.truncate(offset)
                handle.flush()
                os.fsync(handle.fileno())
            break
        offset += len(line)
    return records


def latest_records(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {record["discovery_key"]: record for record in records}


def normalized_input_url(value: str) -> str:
    return value.strip()


def candidate_from_row(row: dict[str, str]) -> Candidate:
    missing = FULL_CANDIDATE_FIELDS - row.keys()
    if missing:
        raise ValueError(f"candidate CSV is missing columns: {sorted(missing)}")
    interval = (
        format_timestamp(row["query_interval_start"]),
        format_timestamp(row["query_interval_end"]),
    )
    if interval not in INTERVALS:
        raise ValueError(f"unexpected query interval: {interval}")
    seen_at = format_timestamp(row["seen_at"])
    url = normalized_input_url(row["url"])
    if not url or not parse_bool(row["has_sk"]):
        raise ValueError("every candidate must have a URL and has_sk=true")
    flags = {name: parse_bool(row[column]) for name, column in PARTNER_FLAGS.items()}
    return Candidate(
        interval[0],
        interval[1],
        seen_at,
        url,
        flags,
        discovery_key(interval[0], interval[1], seen_at, url),
    )


def candidate_from_sample_row(row: dict[str, str]) -> Candidate:
    missing = SAMPLE_CANDIDATE_FIELDS - row.keys()
    if missing:
        raise ValueError(f"sample CSV is missing columns: {sorted(missing)}")
    stratum = row["stratum"].strip().casefold()
    if stratum not in {"base", *PARTNER_ALIASES}:
        raise ValueError(f"unexpected sample stratum: {stratum}")
    interval_start, interval_end = INTERVALS[0]
    seen_at = format_timestamp(row["seen_at"])
    url = normalized_input_url(row["url"])
    if not url:
        raise ValueError("every candidate must have a URL")
    flags = {name: name == stratum for name in PARTNER_ALIASES}
    return Candidate(
        interval_start,
        interval_end,
        seen_at,
        url,
        flags,
        discovery_key(interval_start, interval_end, seen_at, url),
    )


def candidate_csv_mode(path: Path) -> str:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        fields = set(csv.DictReader(handle).fieldnames or ())
    if SAMPLE_CANDIDATE_FIELDS <= fields:
        return "reused_stratified_sample"
    if FULL_CANDIDATE_FIELDS <= fields:
        return "full_export"
    raise ValueError(f"candidate CSV has unsupported columns: {path}")


def candidates_from_csv(path: Path) -> list[Candidate]:
    mode = candidate_csv_mode(path)
    converter = (
        candidate_from_sample_row
        if mode == "reused_stratified_sample"
        else candidate_from_row
    )
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return [converter(row) for row in csv.DictReader(handle)]


def load_candidates(paths: list[Path]) -> tuple[list[Candidate], int]:
    discovered: list[Candidate] = []
    for path in paths:
        discovered.extend(candidates_from_csv(path))
    merged: dict[str, Candidate] = {}
    for candidate in discovered:
        current = merged.get(candidate.url)
        if current is None:
            merged[candidate.url] = candidate
            continue
        primary, duplicate = choose_primary(current, candidate)
        primary.flags = {
            name: current.flags[name] or candidate.flags[name]
            for name in PARTNER_ALIASES
        }
        duplicates = [
            *current.duplicate_discoveries,
            *candidate.duplicate_discoveries,
        ]
        if primary.discovery_key != duplicate.discovery_key:
            duplicates.append(discovery_record(duplicate))
        primary.duplicate_discoveries = list(
            {item["discovery_key"]: item for item in duplicates}.values()
        )
        merged[candidate.url] = primary
    return list(merged.values()), len(discovered)


def choose_primary(left: Candidate, right: Candidate) -> tuple[Candidate, Candidate]:
    left_key = (parse_timestamp(left.seen_at), left.interval_start, left.discovery_key)
    right_key = (
        parse_timestamp(right.seen_at),
        right.interval_start,
        right.discovery_key,
    )
    return (left, right) if left_key <= right_key else (right, left)


def discovery_record(candidate: Candidate) -> dict[str, str]:
    return {
        "discovery_key": candidate.discovery_key,
        "query_interval_start": candidate.interval_start,
        "query_interval_end": candidate.interval_end,
        "seen_at": candidate.seen_at,
        "original_url": candidate.url,
    }


def host_for_order(url: str) -> str:
    return (urllib.parse.urlsplit(url).hostname or "").casefold()


def fair_order(candidates: list[Candidate]) -> list[Candidate]:
    buckets: dict[tuple[str, str], deque[Candidate]] = {}
    for (interval, host), values in _candidate_groups(candidates).items():
        buckets[(interval, host)] = deque(
            sorted(values, key=lambda item: sha256_text(item.url))
        )
    hosts = {
        interval: deque(
            sorted({host for current, host in buckets if current == interval})
        )
        for interval, _ in INTERVALS
    }
    ordered: list[Candidate] = []
    while buckets:
        for interval, _ in INTERVALS:
            candidate = pop_interval_candidate(interval, hosts[interval], buckets)
            if candidate is not None:
                ordered.append(candidate)
    return ordered


def _candidate_groups(
    candidates: list[Candidate],
) -> dict[tuple[str, str], list[Candidate]]:
    grouped: dict[tuple[str, str], list[Candidate]] = defaultdict(list)
    for candidate in candidates:
        grouped[(candidate.interval_start, host_for_order(candidate.url))].append(
            candidate
        )
    return grouped


def pop_interval_candidate(
    interval: str,
    hosts: deque[str],
    buckets: dict[tuple[str, str], deque[Candidate]],
) -> Candidate | None:
    for _ in range(len(hosts)):
        host = hosts.popleft()
        bucket = buckets[(interval, host)]
        candidate = bucket.popleft()
        if bucket:
            hosts.append(host)
        else:
            del buckets[(interval, host)]
        return candidate
    return None


def strip_www(hostname: str) -> str:
    return hostname.removeprefix("www.")


def validate_public_https_url(url: str) -> urllib.parse.SplitResult:
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme.casefold() != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise ValueError("URL must be credential-free HTTPS")
    try:
        addresses = socket.getaddrinfo(
            parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM
        )
    except socket.gaierror as error:
        raise ValueError("URL hostname could not be resolved") from error
    if not addresses or any(
        not ipaddress.ip_address(item[4][0]).is_global for item in addresses
    ):
        raise ValueError("URL must resolve only to public addresses")
    return parsed


def upgraded_https_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url.strip())
    if parsed.scheme.casefold() not in {"http", "https"}:
        raise ValueError("unsupported URL scheme")
    if not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("invalid URL authority")
    scheme = "https"
    upgraded = urllib.parse.urlunsplit(
        (scheme, parsed.netloc, parsed.path or "/", parsed.query, "")
    )
    validate_public_https_url(upgraded)
    return upgraded


def url_origin(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    hostname = (parsed.hostname or "").encode("idna").decode("ascii").casefold()
    port = f":{parsed.port}" if parsed.port and parsed.port != 443 else ""
    return f"https://{hostname}{port}"


def normalize_percent_encoding(value: str) -> str:
    unreserved = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"

    def replace(match: re.Match[str]) -> str:
        character = chr(int(match.group(1), 16))
        return character if character in unreserved else f"%{match.group(1).upper()}"

    return re.sub(r"%([0-9a-fA-F]{2})", replace, value)


def canonicalize_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    hostname = (parsed.hostname or "").encode("idna").decode("ascii").casefold()
    port = f":{parsed.port}" if parsed.port and parsed.port != 443 else ""
    path = normalize_percent_encoding(parsed.path or "/")
    pairs = [
        (key, value)
        for key, value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        if not key.casefold().startswith("utm_")
        and key.casefold() not in TRACKING_PARAMETERS
    ]
    query = urllib.parse.urlencode(sorted(pairs), doseq=True)
    return urllib.parse.urlunsplit(("https", f"{hostname}{port}", path, query, ""))


def safe_document_canonical(document_url: str | None, final_url: str) -> str:
    if not document_url:
        return canonicalize_url(final_url)
    candidate = urllib.parse.urljoin(final_url, document_url)
    try:
        parsed = urllib.parse.urlsplit(candidate)
        final = urllib.parse.urlsplit(final_url)
        if strip_www((parsed.hostname or "").casefold()) != strip_www(
            (final.hostname or "").casefold()
        ):
            return canonicalize_url(final_url)
        return canonicalize_url(candidate)
    except (UnicodeError, ValueError):
        return canonicalize_url(final_url)


def fetch_bytes(
    opener: urllib.request.OpenerDirector,
    url: str,
    maximum: int,
    accepted_content_types: tuple[str, ...] | None,
) -> dict[str, Any]:
    validate_public_https_url(url)
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"},
    )
    with opener.open(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
        final_url = response.geturl()
        validate_public_https_url(final_url)
        content_type = response.headers.get_content_type().casefold()
        if accepted_content_types and content_type not in accepted_content_types:
            raise ValueError(f"unsupported content type: {content_type}")
        content_length = response.headers.get("Content-Length")
        if content_length and int(content_length) > maximum:
            raise ValueError("response exceeds byte limit")
        body = response.read(maximum + 1)
        if len(body) > maximum:
            raise ValueError("response exceeds byte limit")
        return {
            "body": body,
            "final_url": final_url,
            "http_status": response.status,
            "content_type": content_type,
        }


def robots_delay(parser: urllib.robotparser.RobotFileParser) -> float:
    values = [DEFAULT_ORIGIN_DELAY_SECONDS]
    for agent in (USER_AGENT, "*"):
        crawl_delay = parser.crawl_delay(agent)
        if crawl_delay is not None:
            values.append(float(crawl_delay))
        request_rate = parser.request_rate(agent)
        if request_rate and request_rate.requests > 0:
            values.append(float(request_rate.seconds) / request_rate.requests)
    return max(values)


def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize(
        "NFC", value.replace("\r\n", "\n").replace("\r", "\n")
    )
    lines = [line.rstrip() for line in normalized.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def alias_present(value: str, aliases: tuple[str, ...]) -> bool:
    folded = value.casefold()
    return any(alias.casefold() in folded for alias in aliases)


def partner_tags(value: str) -> list[str]:
    return [
        name
        for name, aliases in PARTNER_ALIASES.items()
        if alias_present(value, aliases)
    ]


def relevance_for(title: str, body: str, policy: str) -> str:
    if alias_present(title, SK_ALIASES):
        return "DIRECT"
    if policy == "lead":
        paragraphs = [part for part in re.split(r"\n\s*\n|\n", body) if part.strip()]
        if alias_present("\n".join(paragraphs[:2]), SK_ALIASES):
            return "DIRECT"
    return "BACKGROUND" if alias_present(body, SK_ALIASES) else "IRRELEVANT"


def partner_contexts(title: str, body: str) -> list[str]:
    contexts = [title]
    contexts.extend(part for part in re.split(r"\n\s*\n|\n", body) if part.strip())
    valid: list[str] = []
    for name, aliases in PARTNER_ALIASES.items():
        if any(
            alias_present(context, SK_ALIASES) and alias_present(context, aliases)
            for context in contexts
        ):
            valid.append(name)
    return valid


def language_metrics(body: str) -> tuple[int, int, float]:
    hangul = sum("가" <= character <= "힣" for character in body)
    alphabetic = sum(character.isalpha() for character in body)
    return hangul, alphabetic, hangul / alphabetic if alphabetic else 0.0


def parse_published_at(value: str | None) -> tuple[str, str, datetime] | None:
    if not value:
        return None
    stripped = value.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", stripped):
        parsed = datetime.fromisoformat(stripped).replace(tzinfo=UTC)
        return stripped, "DAY", parsed
    try:
        parsed = datetime.fromisoformat(stripped.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    utc = parsed.astimezone(UTC)
    return utc.isoformat().replace("+00:00", "Z"), "INSTANT", utc


def base_record(candidate: Candidate) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "discovery_key": candidate.discovery_key,
        "source_table": SOURCE_TABLE,
        "query_interval_start": candidate.interval_start,
        "query_interval_end": candidate.interval_end,
        "seen_at": candidate.seen_at,
        "original_url": candidate.url,
        "final_url": "",
        "canonical_url": "",
        "title": "",
        "publisher": "",
        "published_at": "",
        "published_precision": "",
        "collected_at": utc_now(),
        "partner_tags": [],
        "partner_slot": "",
        "relevance": "",
        "robots_allowed": None,
        "http_status": None,
        "status": "",
        "failure_reason": "",
        "duplicate_of": "",
        "body_sha256": "",
        "body_bytes": 0,
        "artifact_key": "",
        "hangul_chars": 0,
        "alphabetic_chars": 0,
        "hangul_ratio": 0.0,
    }


def failed_record(candidate: Candidate, status: str, reason: str) -> dict[str, Any]:
    record = base_record(candidate)
    record.update(status=status, failure_reason=reason)
    return record


def fetch_candidate(
    candidate: Candidate,
    robots: RobotsCache,
    record: dict[str, Any],
) -> dict[str, Any] | None:
    try:
        url = upgraded_https_url(candidate.url)
    except ValueError as error:
        record.update(status="FAILED_HTTP", failure_reason=f"unsafe_url:{error}")
        return None
    robots_policy = robots.policy_for(url)
    record["robots_allowed"] = robots_policy.allowed
    if not robots_policy.allowed:
        record.update(status="BLOCKED_ROBOTS", failure_reason=robots_policy.reason)
        return None
    try:
        return robots.fetch(url, robots_policy.delay)
    except urllib.error.HTTPError as error:
        record["http_status"] = error.code
        status = "RETRY_PENDING" if error.code >= 500 else "FAILED_HTTP"
        record.update(status=status, failure_reason=f"http_{error.code}")
    except (OSError, ValueError) as error:
        retryable = isinstance(
            error, (TimeoutError, urllib.error.URLError, ConnectionError)
        )
        record.update(
            status="RETRY_PENDING" if retryable else "FAILED_HTTP",
            failure_reason=f"{type(error).__name__}:{error}",
        )
    return None


def extract_document(response: dict[str, Any], record: dict[str, Any]) -> Any | None:
    try:
        document = bare_extraction(
            response["body"],
            url=response["final_url"],
            include_comments=False,
            include_tables=True,
            favor_precision=True,
            with_metadata=True,
        )
    except (AttributeError, TypeError, UnicodeError, ValueError) as error:
        record.update(
            status="FAILED_EXTRACTION",
            failure_reason=f"extractor:{type(error).__name__}",
        )
        return None
    if document is None or not getattr(document, "text", None):
        record.update(status="FAILED_EXTRACTION", failure_reason="empty_main_text")
        return None
    return document


def extract_candidate(
    candidate: Candidate,
    robots: RobotsCache,
    policy: str,
) -> tuple[dict[str, Any], str | None]:
    record = base_record(candidate)
    response = fetch_candidate(candidate, robots, record)
    if response is None:
        return record, None
    record.update(final_url=response["final_url"], http_status=response["http_status"])
    document = extract_document(response, record)
    if document is None:
        return record, None
    body = normalize_text(document.text)
    title = normalize_text(getattr(document, "title", "") or "")
    canonical = safe_document_canonical(
        getattr(document, "url", None), response["final_url"]
    )
    publisher = normalize_text(getattr(document, "sitename", "") or "")
    if not publisher:
        publisher = strip_www(urllib.parse.urlsplit(canonical).hostname or "")
    published = parse_published_at(getattr(document, "date", None))
    tags = partner_tags(f"{title}\n{body}")
    relevance = relevance_for(title, body, policy)
    hangul, alphabetic, ratio = language_metrics(body)
    record.update(
        canonical_url=canonical,
        title=title,
        publisher=publisher,
        partner_tags=tags,
        relevance=relevance,
        body_sha256=sha256_text(body),
        body_bytes=len(body.encode("utf-8")),
        hangul_chars=hangul,
        alphabetic_chars=alphabetic,
        hangul_ratio=round(ratio, 6),
    )
    exclusion = eligibility_exclusion(body, title, published, hangul, ratio, relevance)
    if exclusion:
        record.update(status=exclusion[0], failure_reason=exclusion[1])
        return record, None
    assert published is not None
    record.update(published_at=published[0], published_precision=published[1])
    return record, body


def eligibility_exclusion(
    body: str,
    title: str,
    published: tuple[str, str, datetime] | None,
    hangul: int,
    hangul_ratio: float,
    relevance: str,
) -> tuple[str, str] | None:
    if not title:
        return "FAILED_EXTRACTION", "missing_title"
    if published is None:
        return "EXCLUDED_DATE", "missing_or_imprecise_published_at"
    if not (
        parse_timestamp(WINDOW_START) <= published[2] < parse_timestamp(WINDOW_END)
    ):
        return "EXCLUDED_DATE", "published_at_outside_fixed_window"
    if len(body) < 500:
        return "FAILED_EXTRACTION", "main_text_shorter_than_500_characters"
    if hangul < 100 or hangul_ratio < 0.3:
        return "EXCLUDED_LANGUAGE", "korean_body_threshold_not_met"
    if relevance != "DIRECT":
        return "EXCLUDED_RELEVANCE", relevance.casefold()
    return None


def save_body(root: Path, record: dict[str, Any], body: str) -> None:
    artifact_key = f"bodies/{record['body_sha256']}.txt.gz"
    path = root / artifact_key
    if not path.exists():
        write_private_bytes(path, gzip.compress(body.encode("utf-8"), mtime=0))
    record["artifact_key"] = artifact_key


def terminal_duplicate_records(candidates: list[Candidate]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for candidate in candidates:
        for duplicate in candidate.duplicate_discoveries:
            record = base_record(candidate)
            record.update(duplicate)
            record.update(
                status="DUPLICATE_URL",
                failure_reason="duplicate_gdelt_url_merged",
                duplicate_of=candidate.discovery_key,
                canonical_url=canonicalize_url(
                    candidate.url.replace("http://", "https://", 1)
                ),
            )
            records.append(record)
    return records


def partner_counts(records: dict[str, dict[str, Any]]) -> Counter[str]:
    return Counter(
        record["partner_slot"]
        for record in records.values()
        if record["status"] == "SELECTED" and record.get("partner_slot")
    )


def choose_partner_slot(record: dict[str, Any], body: str, counts: Counter[str]) -> str:
    valid = partner_contexts(record["title"], body)
    missing = [name for name in PARTNER_ALIASES if counts[name] < 3 and name in valid]
    return (
        min(
            missing, key=lambda name: (counts[name], tuple(PARTNER_ALIASES).index(name))
        )
        if missing
        else ""
    )


def candidate_sequence(
    candidates: list[Candidate], completed: set[str]
) -> list[Candidate]:
    ordered = [
        candidate
        for candidate in fair_order(candidates)
        if candidate.discovery_key not in completed
    ]
    partner_first = interleave_partner_candidates(ordered)
    partner_keys = {candidate.discovery_key for candidate in partner_first}
    return partner_first + [
        candidate
        for candidate in ordered
        if candidate.discovery_key not in partner_keys
    ]


def interleave_partner_candidates(ordered: list[Candidate]) -> list[Candidate]:
    partner_groups = tuple(
        tuple(candidate for candidate in ordered if candidate.flags[name])
        for name in PARTNER_ALIASES
    )
    used: set[str] = set()
    partner_first: list[Candidate] = []
    for round_candidates in zip_longest(*partner_groups):
        for candidate in round_candidates:
            if candidate is None or candidate.discovery_key in used:
                continue
            partner_first.append(candidate)
            used.add(candidate.discovery_key)
    return partner_first


def record_collection_metadata(
    root: Path,
    candidate_rows: int,
    unique_candidates: int,
    target: int,
    csv_paths: list[Path],
    query_job_ids: list[str],
) -> None:
    metadata = {
        "schema_version": 1,
        "source_table": SOURCE_TABLE,
        "window_start": WINDOW_START,
        "window_end": WINDOW_END,
        "intervals": INTERVALS,
        "candidate_rows": candidate_rows,
        "unique_candidates": unique_candidates,
        "merged_duplicate_rows": candidate_rows - unique_candidates,
        "target": target,
        "candidate_sources": [
            {"file": path.name, "mode": candidate_csv_mode(path)} for path in csv_paths
        ],
        "query_job_ids": query_job_ids,
        "updated_at": utc_now(),
    }
    write_private_bytes(
        root / "collection.json",
        json.dumps(metadata, ensure_ascii=False, indent=2).encode(),
    )


def selected_index(latest: dict[str, dict[str, Any]], field: str) -> dict[str, str]:
    return {
        record[field]: key
        for key, record in latest.items()
        if record["status"] == "SELECTED"
    }


def resume_collection(root: Path, candidates: list[Candidate]) -> CollectionState:
    checkpoint = root / "checkpoint.jsonl"
    records = load_checkpoint(checkpoint)
    latest = latest_records(records)
    for duplicate in terminal_duplicate_records(candidates):
        if duplicate["discovery_key"] not in latest:
            append_checkpoint(checkpoint, duplicate)
            latest[duplicate["discovery_key"]] = duplicate
    return CollectionState(
        checkpoint=checkpoint,
        latest=latest,
        retries=Counter(
            record["discovery_key"]
            for record in records
            if record["status"] == "RETRY_PENDING"
        ),
        terminal={
            key for key, record in latest.items() if record["status"] != "RETRY_PENDING"
        },
        canonical_seen=selected_index(latest, "canonical_url"),
        body_seen=selected_index(latest, "body_sha256"),
        partner_counts=partner_counts(latest),
    )


def process_collection_candidate(
    candidate: Candidate,
    queue: deque[Candidate],
    state: CollectionState,
    robots: RobotsCache,
    relevance_policy: str,
    root: Path,
    target: int,
) -> None:
    if candidate.discovery_key in state.terminal:
        return
    record, body = extract_candidate(candidate, robots, relevance_policy)
    if record["status"] == "RETRY_PENDING":
        state.retries[candidate.discovery_key] += 1
        if state.retries[candidate.discovery_key] > 1:
            record.update(
                status="FAILED_HTTP",
                failure_reason=f"retry_exhausted:{record['failure_reason']}",
            )
        else:
            queue.append(candidate)
    if body is not None:
        classify_eligible(
            record,
            body,
            state.partner_counts,
            state.canonical_seen,
            state.body_seen,
            root,
        )
    append_checkpoint(state.checkpoint, record)
    state.latest[candidate.discovery_key] = record
    if record["status"] != "RETRY_PENDING":
        state.terminal.add(candidate.discovery_key)
    if record["status"] == "SELECTED":
        state.canonical_seen[record["canonical_url"]] = candidate.discovery_key
        state.body_seen[record["body_sha256"]] = candidate.discovery_key
        if record["partner_slot"]:
            state.partner_counts[record["partner_slot"]] += 1
        print_collection_progress(state.latest, state.partner_counts, target)


def collect(args: argparse.Namespace) -> int:
    root = args.artifact_root.resolve()
    ensure_private_directory(root)
    candidates, candidate_rows = load_candidates(args.candidate_csv)
    if {candidate.interval_start for candidate in candidates} != {
        item[0] for item in INTERVALS
    }:
        raise ValueError("candidate CSVs must cover all six fixed intervals")
    record_collection_metadata(
        root,
        candidate_rows,
        len(candidates),
        args.target,
        args.candidate_csv,
        args.query_job_id,
    )
    state = resume_collection(root, candidates)
    robots = RobotsCache()
    queue = deque(candidate_sequence(candidates, state.terminal))
    while queue and not collection_complete(
        state.latest, args.target, args.smoke_publishers
    ):
        candidate = queue.popleft()
        process_collection_candidate(
            candidate,
            queue,
            state,
            robots,
            args.relevance_policy,
            root,
            args.target,
        )
    if not collection_complete(state.latest, args.target, args.smoke_publishers):
        print_collection_summary(
            state.latest, state.partner_counts, args.target, complete=False
        )
        return 2
    print_collection_summary(
        state.latest, state.partner_counts, args.target, complete=True
    )
    return 0


def classify_eligible(
    record: dict[str, Any],
    body: str,
    counts: Counter[str],
    canonical_seen: dict[str, str],
    body_seen: dict[str, str],
    root: Path,
) -> None:
    if record["canonical_url"] in canonical_seen:
        record.update(
            status="DUPLICATE_URL",
            failure_reason="canonical_url_already_selected",
            duplicate_of=canonical_seen[record["canonical_url"]],
        )
        return
    if record["body_sha256"] in body_seen:
        record.update(
            status="DUPLICATE_BODY",
            failure_reason="normalized_body_already_selected",
            duplicate_of=body_seen[record["body_sha256"]],
        )
        return
    needs_partners = any(counts[name] < 3 for name in PARTNER_ALIASES)
    slot = choose_partner_slot(record, body, counts)
    if needs_partners and not slot:
        record.update(
            status="ELIGIBLE_NOT_SELECTED", failure_reason="partner_reserve_phase"
        )
        return
    record.update(status="SELECTED", partner_slot=slot)
    save_body(root, record, body)


def selected_records(latest: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return [record for record in latest.values() if record["status"] == "SELECTED"]


def collection_complete(
    latest: dict[str, dict[str, Any]],
    target: int,
    smoke_publishers: int,
) -> bool:
    selected = selected_records(latest)
    if smoke_publishers:
        return len({record["publisher"] for record in selected}) >= smoke_publishers
    return len(selected) >= target and all(
        partner_counts(latest)[name] >= 3 for name in PARTNER_ALIASES
    )


def print_collection_progress(
    latest: dict[str, dict[str, Any]],
    partners: Counter[str],
    target: int,
) -> None:
    selected = len(selected_records(latest))
    if selected <= 10 or selected % 25 == 0:
        print(
            f"selected={selected}/{target} partner_slots={dict(partners)}", flush=True
        )


def print_collection_summary(
    latest: dict[str, dict[str, Any]],
    partners: Counter[str],
    target: int,
    *,
    complete: bool,
) -> None:
    statuses = Counter(record["status"] for record in latest.values())
    print(
        json.dumps(
            {
                "complete": complete,
                "selected": statuses["SELECTED"],
                "target": target,
                "partner_slots": dict(partners),
                "statuses": dict(sorted(statuses.items())),
            },
            ensure_ascii=False,
        )
    )


def audit_sample(selected: list[dict[str, Any]]) -> list[dict[str, Any]]:
    chosen = {
        record["discovery_key"]: record
        for record in selected
        if record.get("partner_slot")
    }
    add_audit_strata(chosen, selected)
    fill_audit_sample(chosen, selected)
    if len(chosen) != 100:
        raise ValueError(f"audit requires 100 selected documents, found {len(chosen)}")
    return sorted(
        chosen.values(),
        key=lambda item: sha256_text(f"{AUDIT_SALT}:{item['canonical_url']}"),
    )


def add_audit_strata(
    chosen: dict[str, dict[str, Any]], selected: list[dict[str, Any]]
) -> None:
    for interval, _ in INTERVALS:
        add_lowest_hash(
            chosen,
            [item for item in selected if item["query_interval_start"] == interval],
            10,
        )
    publishers = Counter(record["publisher"] for record in selected)
    for publisher, _ in publishers.most_common(10):
        add_lowest_hash(
            chosen, [item for item in selected if item["publisher"] == publisher], 1
        )


def fill_audit_sample(
    chosen: dict[str, dict[str, Any]], selected: list[dict[str, Any]]
) -> None:
    remaining = sorted(
        selected,
        key=lambda item: sha256_text(f"{AUDIT_SALT}:{item['canonical_url']}"),
    )
    for record in remaining:
        if len(chosen) == 100:
            break
        chosen.setdefault(record["discovery_key"], record)


def add_lowest_hash(
    chosen: dict[str, dict[str, Any]],
    candidates: list[dict[str, Any]],
    minimum: int,
) -> None:
    present = sum(record["discovery_key"] in chosen for record in candidates)
    ordered = sorted(
        candidates,
        key=lambda item: sha256_text(f"{AUDIT_SALT}:{item['canonical_url']}"),
    )
    for record in ordered:
        if present >= minimum:
            return
        if record["discovery_key"] not in chosen:
            chosen[record["discovery_key"]] = record
            present += 1
    if present < minimum:
        raise ValueError("selected corpus cannot satisfy audit strata")


def write_audit(path: Path, rows: list[dict[str, Any]]) -> None:
    ensure_private_directory(path.parent)
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        os.chmod(handle.fileno(), 0o600)
        writer = csv.DictWriter(handle, fieldnames=AUDIT_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)
    path.chmod(0o600)


def read_audit(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def audit(args: argparse.Namespace) -> int:
    root = args.artifact_root.resolve()
    path = root / "audit.csv"
    if args.record:
        if not path.exists():
            raise ValueError("create the audit sample before recording a decision")
        update_audit_record(path, args)
    elif not path.exists():
        latest = latest_records(load_checkpoint(root / "checkpoint.jsonl"))
        sample = audit_sample(selected_records(latest))
        rows = [
            {
                **{field: record.get(field, "") for field in AUDIT_FIELDS},
                **empty_audit_decisions(),
            }
            for record in sample
        ]
        write_audit(path, rows)
        print(f"created {path} with {len(rows)} rows")
    else:
        print(f"using existing {path}")
    print(json.dumps(audit_summary(read_audit(path)), ensure_ascii=False))
    return 0


def empty_audit_decisions() -> dict[str, str]:
    return {
        "manual_date_ok": "",
        "manual_korean_ok": "",
        "manual_extraction_ok": "",
        "manual_direct_relevance": "",
        "manual_partner_valid": "",
        "notes": "",
    }


def update_audit_record(path: Path, args: argparse.Namespace) -> None:
    rows = read_audit(path)
    matches = [row for row in rows if row["discovery_key"] == args.record]
    if len(matches) != 1:
        raise ValueError("--record must identify exactly one audit row")
    decision = matches[0]
    decision.update(
        manual_date_ok=args.date_ok,
        manual_korean_ok=args.korean_ok,
        manual_extraction_ok=args.extraction_ok,
        manual_direct_relevance=args.direct_relevance,
        manual_partner_valid=args.partner_valid,
        notes=args.notes or "",
    )
    write_audit(path, rows)


def yes(value: str) -> bool:
    return value.strip().casefold() == "yes"


def completed_audit_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    decision_fields = AUDIT_FIELDS[9:14]
    return [row for row in rows if all(row[field] for field in decision_fields)]


def count_yes(rows: list[dict[str, str]], field: str) -> int:
    return sum(yes(row[field]) for row in rows)


def audit_summary(rows: list[dict[str, str]]) -> dict[str, Any]:
    complete = completed_audit_rows(rows)
    partner_rows = [row for row in rows if row["partner_slot"]]
    return {
        "rows": len(rows),
        "completed": len(complete),
        "date_ok": count_yes(complete, "manual_date_ok"),
        "korean_ok": count_yes(complete, "manual_korean_ok"),
        "extraction_ok": count_yes(complete, "manual_extraction_ok"),
        "direct_relevance_ok": count_yes(complete, "manual_direct_relevance"),
        "partner_rows": len(partner_rows),
        "partner_valid": count_yes(partner_rows, "manual_partner_valid"),
    }


def validate_audit(rows: list[dict[str, str]]) -> None:
    summary = audit_summary(rows)
    if summary["rows"] != 100 or summary["completed"] != 100:
        raise ValueError("manual audit must contain 100 completed rows")
    if min(summary["date_ok"], summary["korean_ok"], summary["extraction_ok"]) != 100:
        raise ValueError("manual date, Korean, and extraction checks must pass 100/100")
    if summary["direct_relevance_ok"] < 90:
        raise ValueError("manual direct relevance must pass at least 90/100")
    if summary["partner_rows"] != 9 or summary["partner_valid"] != 9:
        raise ValueError("all nine reserved partner documents must pass manual audit")


def validate_unique_selected_field(
    selected: list[dict[str, Any]], field: str, target: int
) -> None:
    if len({item[field] for item in selected}) != target:
        raise ValueError(f"selected {field} values are not unique")


def validate_final(
    metadata: dict[str, Any],
    latest: dict[str, dict[str, Any]],
    audit_rows: list[dict[str, str]],
) -> list[dict[str, Any]]:
    selected = selected_records(latest)
    target = metadata["target"]
    if len(selected) != target:
        raise ValueError(
            f"expected exactly {target} selected documents, found {len(selected)}"
        )
    validate_unique_selected_field(selected, "canonical_url", target)
    validate_unique_selected_field(selected, "body_sha256", target)
    validate_selected_records(selected)
    validate_audit(audit_rows)
    return selected


def validate_selected_record(record: dict[str, Any]) -> None:
    if not record["title"]:
        raise ValueError(f"missing title for {record['discovery_key']}")
    published = parse_published_at(record["published_at"])
    if published is None or not (
        parse_timestamp(WINDOW_START) <= published[2] < parse_timestamp(WINDOW_END)
    ):
        raise ValueError(f"invalid published_at for {record['discovery_key']}")
    if (
        record["relevance"] != "DIRECT"
        or record["hangul_chars"] < 100
        or record["hangul_ratio"] < 0.3
    ):
        raise ValueError(f"invalid eligibility for {record['discovery_key']}")


def validate_selected_records(selected: list[dict[str, Any]]) -> None:
    counts = Counter(
        record["partner_slot"] for record in selected if record["partner_slot"]
    )
    if any(counts[name] < 3 for name in PARTNER_ALIASES):
        raise ValueError(
            "selected corpus does not contain three reserved documents per partner"
        )
    for record in selected:
        validate_selected_record(record)


def public_record(record: dict[str, Any]) -> dict[str, Any]:
    result = {field: record.get(field, "") for field in PUBLIC_FIELDS}
    if result["status"] == "RETRY_PENDING":
        result["status"] = "FAILED_HTTP"
        result["failure_reason"] = f"transient_retry_pending:{result['failure_reason']}"
    if result["status"] not in PUBLIC_STATUSES:
        raise ValueError(f"unknown public status: {result['status']}")
    return result


def write_public_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    value = "".join(
        f"{json.dumps(public_record(record), ensure_ascii=False, sort_keys=True, separators=(',', ':'))}\n"
        for record in sorted(records, key=lambda item: item["discovery_key"])
    )
    path.write_text(value, encoding="utf-8")


def finalize(args: argparse.Namespace) -> int:
    root = args.artifact_root.resolve()
    output = args.output.resolve()
    metadata = json.loads((root / "collection.json").read_text(encoding="utf-8"))
    latest = latest_records(load_checkpoint(root / "checkpoint.jsonl"))
    audit_rows = read_audit(root / "audit.csv")
    selected = validate_final(metadata, latest, audit_rows)
    output.mkdir(parents=True, exist_ok=True)
    manifest = output / "manifest.jsonl"
    write_public_jsonl(manifest, list(latest.values()))
    write_public_audit(output / "audit.csv", audit_rows)
    snapshot_id = hashlib.sha256(manifest.read_bytes()).hexdigest()[:16]
    (output / "README.md").write_text(
        render_report(metadata, latest, selected, audit_rows, snapshot_id),
        encoding="utf-8",
    )
    write_checksums(output)
    print(
        json.dumps(
            {"snapshot_id": snapshot_id, "selected": len(selected)}, ensure_ascii=False
        )
    )
    return 0


def write_public_audit(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=AUDIT_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def markdown_counts(counter: Counter[str], limit: int | None = None) -> str:
    items = counter.most_common(limit)
    return "\n".join(f"- `{key or '(없음)'}`: {value}" for key, value in items)


def report_counts(
    latest: dict[str, dict[str, Any]], selected: list[dict[str, Any]]
) -> dict[str, Counter[str]]:
    return {
        "statuses": Counter(record["status"] for record in latest.values()),
        "dates": Counter(record["published_at"][:10] for record in selected),
        "publishers": Counter(record["publisher"] for record in selected),
        "partners": Counter(
            tag for record in selected for tag in record["partner_tags"]
        ),
        "slots": Counter(
            record["partner_slot"] for record in selected if record["partner_slot"]
        ),
    }


def metadata_list(values: list[Any], formatter: Any) -> str:
    return "\n".join(formatter(value) for value in values) or "- 기록 없음"


def render_report(
    metadata: dict[str, Any],
    latest: dict[str, dict[str, Any]],
    selected: list[dict[str, Any]],
    audit_rows: list[dict[str, str]],
    snapshot_id: str,
) -> str:
    counts = report_counts(latest, selected)
    audit_result = audit_summary(audit_rows)
    unprocessed = metadata["unique_candidates"] - (
        len(latest) - metadata["merged_duplicate_rows"]
    )
    jobs = metadata_list(metadata.get("query_job_ids", []), lambda job: f"- `{job}`")
    candidate_sources = metadata_list(
        metadata.get("candidate_sources", []),
        lambda source: f"- `{source['file']}`: `{source['mode']}`",
    )
    return f"""# Issue #131 GDELT 수집 결과

## 범위와 결과

- snapshot ID: `{snapshot_id}`
- source: `{metadata["source_table"]}`
- 발행 범위: `{metadata["window_start"]} <= published_at < {metadata["window_end"]}`
- 입력 후보 행: {metadata["candidate_rows"]:,}
- 병합 후 후보 URL: {metadata["unique_candidates"]:,}
- 병합한 중복 발견 행: {metadata["merged_duplicate_rows"]:,}
- 목표 충족 후 원문 요청을 생략한 후보 URL: {unprocessed:,}
- 최종 선택: {len(selected):,}
- 원문 보관: 저장소 밖 접근 제한 경로. `manifest.jsonl`의 `artifact_key`로만 참조한다.

개발 fixture나 모델 산출물이 아니라 공개 원문을 수집·정규화한 일회성 시연 corpus다. 이 결과는 추출 Agent의 의미 품질이나 제품 DB publication 완료를 증명하지 않으며, 다음 단계인 #110의 입력 근거로만 사용한다. 이 스크립트는 운영 수집기가 아니며 향후 제품 입력은 외부에서 모집·제공한 자료를 받는다.

## BigQuery 작업

첫 구간은 #112에서 만든 고정 35행 층화 표본을 재사용했고, 나머지 다섯 구간은 [query.sql](query.sql)의 전체 후보 SQL로 내보냈다. 새 BigQuery 실행은 각각 최대 처리 바이트 50 GiB로 제한했다.

{candidate_sources}

{jobs}

## 처리 상태

{markdown_counts(counts["statuses"])}

## 수동 감사

- 표본: {audit_result["rows"]}건
- 발행일·한국어·본문 추출: 각각 {audit_result["date_ok"]}/{audit_result["rows"]}, {audit_result["korean_ok"]}/{audit_result["rows"]}, {audit_result["extraction_ok"]}/{audit_result["rows"]}
- 직접 관련성: {audit_result["direct_relevance_ok"]}/{audit_result["rows"]} (합격 기준 90건 이상)
- 파트너 예약 자료: {audit_result["partner_valid"]}/{audit_result["partner_rows"]}

표본은 파트너 예약 9건 전체, 여섯 구간별 최소 10건, 상위 10개 publisher별 최소 1건을 포함하고 나머지는 고정 salt의 URL hash 순서로 선택했다. 감사 판단과 메모는 [audit.csv](audit.csv)에 있다.

## 분포

### 날짜

{markdown_counts(counts["dates"])}

### 상위 publisher 20개

{markdown_counts(counts["publishers"], 20)}

### 파트너 본문 태그

{markdown_counts(counts["partners"])}

### 파트너 예약 슬롯

{markdown_counts(counts["slots"])}

## 재현과 무결성

```bash
uv run --script scripts/collect_gdelt.py self-check
uv run --script scripts/collect_gdelt.py collect --artifact-root <외부 경로> --candidate-csv <표본 CSV 1개와 전체 CSV 5개> --target 1000 --query-job-id <후보 소스 작업 ID 6개>
uv run --script scripts/collect_gdelt.py audit --artifact-root <외부 경로>
uv run --script scripts/collect_gdelt.py finalize --artifact-root <외부 경로> --output review/131-gdelt-collection
```

`checksums.sha256`는 공개 산출물의 SHA-256을 기록한다. 원문 본문과 checkpoint는 Git·Issue·PR에 게시하지 않는다.
"""


def write_checksums(output: Path) -> None:
    names = ["README.md", "audit.csv", "manifest.jsonl", "query.sql"]
    missing = [name for name in names if not (output / name).exists()]
    if missing:
        raise ValueError(f"public output is missing files: {missing}")
    lines = [
        f"{hashlib.sha256((output / name).read_bytes()).hexdigest()}  {name}"
        for name in names
    ]
    (output / "checksums.sha256").write_text(f"{'\n'.join(lines)}\n", encoding="utf-8")


def self_check(_: argparse.Namespace) -> int:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        candidates = check_candidate_loading(root)
        check_normalization_and_order(candidates)
        check_metadata_extraction()
        check_checkpoint_and_public_record(root, candidates[0])
    print("self-check passed")
    return 0


def check_candidate_loading(root: Path) -> list[Candidate]:
    csv_path = root / "candidates.csv"
    csv_path.write_text(self_check_csv(), encoding="utf-8")
    candidates, rows = load_candidates([csv_path])
    assert rows == 3 and len(candidates) == 2
    merged = next(candidate for candidate in candidates if "a.example" in candidate.url)
    assert merged.seen_at == "2026-06-12T00:00:00Z"
    assert merged.flags == {"samsung": True, "intel": True, "nvidia": False}
    assert len(merged.duplicate_discoveries) == 1

    sample_path = root / "sample.csv"
    sample_path.write_text(self_check_sample_csv(), encoding="utf-8")
    sample, sample_rows = load_candidates([sample_path])
    assert sample_rows == 3 and len(sample) == 1
    assert sample[0].flags == {"samsung": True, "intel": True, "nvidia": False}
    assert not sample[0].duplicate_discoveries
    return candidates


def check_normalization_and_order(candidates: list[Candidate]) -> None:
    composed = normalize_text("e\u0301\r\n\r\n\r\nSK하이닉스 ")
    assert composed == "é\n\nSK하이닉스"
    assert sha256_text(composed) == hashlib.sha256(composed.encode()).hexdigest()
    assert canonicalize_url("https://EXAMPLE.com:443/a%7Eb?utm_source=x&b=2&a=1#x") == (
        "https://example.com/a~b?a=1&b=2"
    )
    assert [item.discovery_key for item in fair_order(candidates)] == [
        item.discovery_key for item in fair_order(list(reversed(candidates)))
    ]


def check_metadata_extraction() -> None:
    html = f"""<html><head><title>SK하이닉스 기사</title>
<meta property="article:published_time" content="2026-07-23T06:10:00+09:00">
</head><body><article><p>{"가" * 600}</p></article></body></html>"""
    document = extract_document(
        {"body": html.encode(), "final_url": "https://example.com/article"}, {}
    )
    assert document is not None
    assert document.date == "2026-07-23" and document.title == "SK하이닉스 기사"


def check_checkpoint_and_public_record(root: Path, candidate: Candidate) -> None:
    checkpoint = root / "checkpoint.jsonl"
    first = failed_record(candidate, "FAILED_HTTP", "test")
    append_checkpoint(checkpoint, first)
    with checkpoint.open("ab") as handle:
        handle.write(b'{"broken"')
    assert load_checkpoint(checkpoint) == [first]
    public = public_record({**first, "body": "must not leak"})
    assert "body" not in public and set(public) == set(PUBLIC_FIELDS)


def self_check_csv() -> str:
    return """query_interval_start,query_interval_end,seen_at,url,has_sk,has_samsung,has_intel,has_nvidia
2026-06-11T00:00:00Z,2026-06-26T00:00:00Z,2026-06-13T00:00:00Z,https://a.example/one,true,true,false,false
2026-06-11T00:00:00Z,2026-06-26T00:00:00Z,2026-06-12T00:00:00Z,https://a.example/one,true,false,true,false
2026-06-11T00:00:00Z,2026-06-26T00:00:00Z,2026-06-14T00:00:00Z,https://b.example/two,true,false,false,true
"""


def self_check_sample_csv() -> str:
    return """stratum,sample_rank,candidate_count,url,seen_at
base,1,100,https://sample.example/one,2026-06-12 00:00:00 UTC
samsung,1,10,https://sample.example/one,2026-06-12 00:00:00 UTC
intel,1,10,https://sample.example/one,2026-06-12 00:00:00 UTC
"""


def add_common_root(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--artifact-root", type=Path, required=True)


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(description=__doc__)
    subcommands = command.add_subparsers(dest="command", required=True)

    collect_parser = subcommands.add_parser(
        "collect", help="collect normalized article bodies"
    )
    add_common_root(collect_parser)
    collect_parser.add_argument(
        "--candidate-csv", type=Path, action="append", required=True
    )
    collect_parser.add_argument("--query-job-id", action="append", default=[])
    collect_parser.add_argument("--target", type=int, default=1000)
    collect_parser.add_argument("--smoke-publishers", type=int, default=0)
    collect_parser.add_argument(
        "--relevance-policy", choices=("lead", "title"), default="lead"
    )
    collect_parser.set_defaults(run=collect)

    audit_parser = subcommands.add_parser(
        "audit", help="create or update the deterministic audit"
    )
    add_common_root(audit_parser)
    audit_parser.add_argument("--record")
    for option in ("date-ok", "korean-ok", "extraction-ok", "direct-relevance"):
        audit_parser.add_argument(f"--{option}", choices=("yes", "no"), required=False)
    audit_parser.add_argument(
        "--partner-valid", choices=("yes", "no", "na"), required=False
    )
    audit_parser.add_argument("--notes")
    audit_parser.set_defaults(run=audit)

    finalize_parser = subcommands.add_parser(
        "finalize", help="validate and publish sanitized files"
    )
    add_common_root(finalize_parser)
    finalize_parser.add_argument("--output", type=Path, required=True)
    finalize_parser.set_defaults(run=finalize)

    check_parser = subcommands.add_parser(
        "self-check", help="run the offline invariant check"
    )
    check_parser.set_defaults(run=self_check)
    return command


def validate_args(args: argparse.Namespace) -> None:
    if args.command == "collect" and (args.target < 1 or args.smoke_publishers < 0):
        raise ValueError("collection target and smoke publisher count must be positive")
    if args.command == "audit" and args.record:
        required = (
            args.date_ok,
            args.korean_ok,
            args.extraction_ok,
            args.direct_relevance,
            args.partner_valid,
        )
        if any(value is None for value in required):
            raise ValueError(
                "recording an audit decision requires every manual decision field"
            )


def main() -> int:
    args = parser().parse_args()
    try:
        validate_args(args)
        return args.run(args)
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
