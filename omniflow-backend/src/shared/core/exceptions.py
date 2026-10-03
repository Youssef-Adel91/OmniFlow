"""Domain exceptions shared across layers."""
from __future__ import annotations


class NotFoundError(ValueError):
    """
    A row/resource does not exist (or is not visible to the current tenant).

    Subclasses ValueError only so older `except ValueError` call sites (e.g.
    the worker that tolerates a missing conversation) keep working. The
    gateway maps *this* type to HTTP 404; a bare ValueError is a bug and
    surfaces as a logged 500 instead of masquerading as "not found".
    """
