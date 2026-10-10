"""
Reusable bulk-import engine (file -> table -> column mapping -> validation).

Used by the property import (prompt 2) and designed for the customer import
(prompt 3): everything entity-specific lives in a `schema.ImportSchema`; the
parsing, fuzzy mapping, normalisation and validation here are generic.
"""
