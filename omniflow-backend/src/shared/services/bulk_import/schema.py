"""Declarative description of an importable entity (properties now, customers next)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

Parser = Callable[[object], Any]


@dataclass(frozen=True)
class FieldSpec:
    name: str
    label_ar: str
    synonyms: tuple[str, ...] = ()
    required: bool = False
    example: str = ""
    # Raises text.ParseError (user-facing Arabic message) on a bad cell.
    parse: Parser | None = None
    # A composite column (e.g. "lat, lng") fills several canonical fields.
    outputs: tuple[str, ...] = ()
    # Use `options["defaults"][name]` when the cell is blank or the column unmapped.
    allow_default: bool = True

    @property
    def targets(self) -> tuple[str, ...]:
        return self.outputs or (self.name,)


@dataclass(frozen=True)
class ImportSchema:
    kind: str
    fields: tuple[FieldSpec, ...]
    # Canonical field(s) forming the natural key used for duplicate handling.
    key_field: str | None = None
    # Optional hook: post-process a parsed row (cross-field rules). Returns warnings.
    finalize: Callable[[dict[str, Any]], list[str]] | None = field(default=None, compare=False)

    def by_name(self) -> dict[str, FieldSpec]:
        return {f.name: f for f in self.fields}
