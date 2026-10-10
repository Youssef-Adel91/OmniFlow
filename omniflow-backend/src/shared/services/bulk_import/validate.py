"""Apply a column mapping to a table and validate every row."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator

from .files import Table
from .schema import ImportSchema
from .text import ParseError, clean_str


@dataclass
class Issue:
    field: str
    message: str
    severity: str = "error"      # error | warning

    def as_dict(self) -> dict:
        return {"field": self.field, "message": self.message, "severity": self.severity}


@dataclass
class RowResult:
    row_number: int                      # 1-based spreadsheet row (header is row 1)
    values: dict[str, Any] = field(default_factory=dict)
    issues: list[Issue] = field(default_factory=list)
    raw: dict[str, str] = field(default_factory=dict)
    key: str | None = None
    duplicate_in_file_of: int | None = None

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors and self.duplicate_in_file_of is None


def validate_table(
    table: Table,
    mapping: dict[str, str],
    schema: ImportSchema,
    defaults: dict[str, Any] | None = None,
) -> Iterator[RowResult]:
    """
    Yield one RowResult per data row.

    - mapped column + blank cell -> default if one is configured, else (required) error / (optional) skipped
    - required field with neither column nor default -> error on every row
    - rows repeating an earlier row's key (e.g. REGA number) are flagged as in-file duplicates
    """
    defaults = defaults or {}
    col = {h: i for i, h in enumerate(table.headers)}
    specs = schema.by_name()
    first_seen: dict[str, int] = {}

    for idx, cells in enumerate(table.rows):
        rownum = idx + 2
        res = RowResult(row_number=rownum)
        for h, i in col.items():
            if i < len(cells) and cells[i]:
                res.raw[h] = cells[i]

        for spec in schema.fields:
            header = mapping.get(spec.name)
            cell = clean_str(cells[col[header]]) if header and col[header] < len(cells) else None
            if cell is None:
                dv = defaults.get(spec.name) if spec.allow_default else None
                if dv not in (None, ""):
                    cell = str(dv)
                    # defaults are trusted configuration, parse them like any cell
                elif spec.required:
                    res.issues.append(Issue(spec.name, f"{spec.label_ar}: مطلوب"))
                    continue
                else:
                    continue
            try:
                parsed = spec.parse(cell) if spec.parse else cell
            except ParseError as exc:
                res.issues.append(Issue(spec.name, str(exc)))
                continue
            if isinstance(parsed, dict):
                res.values.update(parsed)
            else:
                res.values[spec.name] = parsed

        if schema.finalize and not res.errors:
            for w in schema.finalize(res.values):
                res.issues.append(Issue("", w, "warning"))

        if schema.key_field and res.values.get(schema.key_field):
            key = str(res.values[schema.key_field])
            res.key = key
            if key in first_seen:
                res.duplicate_in_file_of = first_seen[key]
                res.issues.append(Issue(schema.key_field, f"مكرر داخل الملف (نفس الصف {first_seen[key]})", "error"))
            else:
                first_seen[key] = rownum
        yield res
