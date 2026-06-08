"""Structured logging with PHI redaction.

v1 logged raw EDI segments at WARN — including patient names, member IDs,
addresses, and dates of birth. The redaction filter here is the first line
of defense; the second is: do not pass PHI to the logger in the first place.

Call ``configure_logging()`` once at process startup (FastAPI lifespan,
arq worker startup, alembic env.py, scripts).
"""

from __future__ import annotations

import logging
import re
import sys
from typing import Any

import structlog

from rcm.core.config import settings


# Patterns that look like PHI in raw EDI / log strings.
# Greedy but conservative: false positives are better than leakage.
_PHI_PATTERNS = (
    # NPI (10 digits, often after NM1*XX* prefix)
    (re.compile(r"\bNM1\*[A-Z0-9]{2,3}\*[^~]*?\*XX\*(\d{10})\b"), r"NM1***XX***NPI_REDACTED***"),
    # Member ID (NM108=MI)
    (re.compile(r"\b(\*MI\*)([^*~]+)"), r"\1MEMBER_ID_REDACTED"),
    # Patient/subscriber names in NM1 segments (NM103, NM104)
    (re.compile(r"(\bNM1\*(?:IL|QC|01|02)\*[12]\*)([^*~]*)\*([^*~]*)"),
     r"\1NAME_REDACTED*NAME_REDACTED"),
    # DOB (DMG segment with D8 date qualifier)
    (re.compile(r"\bDMG\*D8\*\d{8}"), "DMG*D8*DOB_REDACTED"),
    # SSN-like (loose)
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "***-**-****"),
)


def _redact_phi(value: str) -> str:
    if not value:
        return value
    out = value
    for pat, repl in _PHI_PATTERNS:
        out = pat.sub(repl, out)
    return out


def _phi_redact_processor(_logger: Any, _name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """structlog processor that redacts PHI in known string fields."""
    for key in ("event", "msg", "raw_segment", "raw_text", "segment_text", "claim_text"):
        v = event_dict.get(key)
        if isinstance(v, str):
            event_dict[key] = _redact_phi(v)
    return event_dict


class _PhiRedactingFormatter(logging.Filter):
    """stdlib logging filter — catches anything that bypasses structlog."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = _redact_phi(record.msg)
        if record.args:
            try:
                record.args = tuple(
                    _redact_phi(a) if isinstance(a, str) else a for a in record.args
                )
            except Exception:
                pass
        return True


def configure_logging() -> None:
    level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _phi_redact_processor,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if settings.LOG_FORMAT.lower() == "json":
        renderer: Any = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=False)

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )

    # Bridge stdlib logging into the same pipeline
    handler = logging.StreamHandler(sys.stderr)
    handler.addFilter(_PhiRedactingFormatter())
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s :: %(message)s"))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # Tame noisy libs
    logging.getLogger("sqlalchemy.engine").setLevel(
        logging.INFO if settings.DEBUG else logging.WARNING
    )
    logging.getLogger("asyncpg").setLevel(logging.WARNING)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)  # type: ignore[no-any-return]
