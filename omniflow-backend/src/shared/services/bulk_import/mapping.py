"""Fuzzy column mapping: file headers -> canonical schema fields."""
from __future__ import annotations

import difflib
from dataclasses import dataclass

from .schema import ImportSchema
from .text import norm_text

EXACT = 1.0
CONTAINS = 0.85
FUZZY_MIN = 0.78


@dataclass
class MappingSuggestion:
    field: str
    header: str | None
    confidence: float
    source: str = "auto"      # auto | llm | template | manual

    def as_dict(self) -> dict:
        return {"field": self.field, "header": self.header, "confidence": round(self.confidence, 2), "source": self.source}


def _score(header: str, names: list[str]) -> float:
    h = norm_text(header)
    if not h:
        return 0.0
    best = 0.0
    for n in names:
        if h == n:
            return EXACT
        if len(n) >= 3 and len(h) >= 3 and (n in h.split() or h in n.split() or (len(n) >= 4 and n in h)):
            best = max(best, CONTAINS)
        ratio = difflib.SequenceMatcher(None, h, n).ratio()
        if ratio >= FUZZY_MIN:
            best = max(best, ratio * 0.9)
    return best


def suggest_mapping(headers: list[str], schema: ImportSchema) -> list[MappingSuggestion]:
    """
    One suggestion per schema field (header=None when nothing fits). A header is
    assigned to at most one field; the highest-scoring pairs win.
    """
    candidates: list[tuple[float, str, str]] = []
    for spec in schema.fields:
        names = [norm_text(s) for s in (spec.name, spec.label_ar, *spec.synonyms)]
        for header in headers:
            score = _score(header, names)
            if score > 0:
                candidates.append((score, spec.name, header))
    candidates.sort(key=lambda c: (-c[0], c[1]))
    taken_fields: dict[str, tuple[str, float]] = {}
    taken_headers: set[str] = set()
    for score, fname, header in candidates:
        if fname in taken_fields or header in taken_headers:
            continue
        taken_fields[fname] = (header, score)
        taken_headers.add(header)
    out = []
    for spec in schema.fields:
        header, score = taken_fields.get(spec.name, (None, 0.0))
        out.append(MappingSuggestion(spec.name, header, score))
    return out


def mapping_dict(suggestions: list[MappingSuggestion], min_confidence: float = 0.0) -> dict[str, str]:
    return {s.field: s.header for s in suggestions if s.header and s.confidence >= min_confidence}


def validate_mapping(mapping: dict[str, str | None], headers: list[str], schema: ImportSchema) -> dict[str, str]:
    """Drop empty entries; reject unknown fields / headers (the client controls this input)."""
    known = schema.by_name()
    clean: dict[str, str] = {}
    used: dict[str, str] = {}
    for fname, header in mapping.items():
        if not header:
            continue
        if fname not in known:
            raise ValueError(f"حقل غير معروف في الربط: {fname}")
        if header not in headers:
            raise ValueError(f"العمود «{header}» غير موجود في الملف")
        if header in used:
            raise ValueError(f"العمود «{header}» مربوط بحقلين ({used[header]} و{known[fname].label_ar})")
        used[header] = known[fname].label_ar
        clean[fname] = header
    return clean
