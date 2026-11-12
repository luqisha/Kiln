"""Structured JSON logging with per-job secret redaction.

``redact`` is shared with ``job_logs`` storage so logs and stored output follow one rule.
"""

from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

import structlog
from structlog.typing import EventDict, WrappedLogger

from app.constants import REDACTION_PLACEHOLDER

_job_secrets: ContextVar[frozenset[str]] = ContextVar("job_secrets", default=frozenset())


def redact(text: str, secrets: Iterable[str]) -> str:
    """Replace every occurrence of every secret, including overlapping ones, with a placeholder.

    Matches are merged into spans first, so one secret being a substring of another,
    or two occurrences overlapping, cannot leave a fragment behind.
    """
    spans = _merge_spans(_find_spans(text, secrets))
    if not spans:
        return text
    pieces: list[str] = []
    cursor = 0
    for start, end in spans:
        pieces.append(text[cursor:start])
        pieces.append(REDACTION_PLACEHOLDER)
        cursor = end
    pieces.append(text[cursor:])
    return "".join(pieces)


def _find_spans(text: str, secrets: Iterable[str]) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for secret in secrets:
        if not secret:
            continue
        start = text.find(secret)
        while start != -1:
            spans.append((start, start + len(secret)))
            start = text.find(secret, start + 1)
    return spans


def _merge_spans(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def truncate_tail(text: str, max_bytes: int) -> tuple[str, bool]:
    """Keep the last ``max_bytes`` of UTF-8 text. Returns the text and whether it was cut."""
    encoded = text.encode()
    if len(encoded) <= max_bytes:
        return text, False
    # Dropping a partial leading character is fine: the tail is what matters for debugging.
    # Slice by start index: encoded[-0:] would keep everything.
    return encoded[len(encoded) - max_bytes :].decode(errors="ignore"), True


@contextmanager
def job_secrets(*secrets: str) -> Iterator[None]:
    """Redact these secrets from every log line emitted inside the block."""
    token = _job_secrets.set(_job_secrets.get() | frozenset(secrets))
    try:
        yield
    finally:
        _job_secrets.reset(token)


def _redact_value(value: Any, secrets: frozenset[str]) -> Any:
    if isinstance(value, str):
        return redact(value, secrets)
    if isinstance(value, Mapping):
        return {key: _redact_value(item, secrets) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_redact_value(item, secrets) for item in value]
    return value


def _redact_event(_logger: WrappedLogger, _method: str, event_dict: EventDict) -> EventDict:
    secrets = _job_secrets.get()
    if not secrets:
        return event_dict
    return {key: _redact_value(value, secrets) for key, value in event_dict.items()}


def configure_logging() -> None:
    """Configure structlog for JSON output. Call once at process start (API, worker, beat)."""
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.format_exc_info,
            # Redact after exceptions are rendered to text, so tracebacks are covered too.
            _redact_event,
            structlog.processors.JSONRenderer(),
        ],
        cache_logger_on_first_use=True,
    )
