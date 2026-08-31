"""Structured JSON logging with a request id that follows a request everywhere.

The id is set once in middleware and read from a context variable, so retrieval,
embedding and generation all stamp the same value without threading a parameter
through every call.
"""

from __future__ import annotations

import logging
import sys
from contextvars import ContextVar

import structlog

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
session_id_var: ContextVar[str] = ContextVar("session_id", default="-")


def _add_context(_logger, _name, event_dict):  # noqa: ANN001, ANN202
    event_dict["request_id"] = request_id_var.get()
    session = session_id_var.get()
    if session != "-":
        # Truncated: enough to correlate, not enough to identify.
        event_dict["session"] = session[:8]
    return event_dict


def configure_logging(level: str = "INFO", *, json_logs: bool = True) -> None:
    processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _add_context,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    processors.append(
        structlog.processors.JSONRenderer()
        if json_logs
        else structlog.dev.ConsoleRenderer(colors=True)
    )

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    logging.basicConfig(
        format="%(message)s", stream=sys.stdout,
        level=getattr(logging, level.upper(), logging.INFO),
    )
    # uvicorn's access log duplicates our own request logging.
    logging.getLogger("uvicorn.access").disabled = True


def get_logger(name: str = "nyaya"):  # noqa: ANN201
    return structlog.get_logger(name)
