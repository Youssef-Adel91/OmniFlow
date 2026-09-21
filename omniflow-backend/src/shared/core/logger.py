"""
shared/core/logger.py — Structured logger accessor

Several modules (notably `src/gateway/dependencies.py`) import
`get_logger` from here. The module did not exist, so every code path that
touched it raised `ModuleNotFoundError` at runtime — turning what should have
been a clean HTTP 401 into an HTTP 500. This module closes that gap.

Usage:
    from src.shared.core.logger import get_logger
    logger = get_logger(__name__)
    logger.info("event_name", key="value")
"""
from __future__ import annotations

from typing import Any

import structlog


def get_logger(name: str | None = None, **initial_values: Any) -> Any:
    """
    Return a bound structlog logger.

    Args:
        name: Logger name — pass `__name__` from the calling module.
        **initial_values: Optional context permanently bound to the logger.
    """
    logger = structlog.get_logger(name)
    if initial_values:
        logger = logger.bind(**initial_values)
    return logger


__all__ = ["get_logger"]
