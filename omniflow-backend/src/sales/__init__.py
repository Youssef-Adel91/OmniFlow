"""Sales closing: qualification → listing match → viewing booking → human handoff → follow-up → pipeline.

Pure rules live in `qualification`, `intents` and `booking` (no I/O, deterministic, unit-tested in Arabic dialects). Everything that touches
the database or Redis is in `matcher`, `pipeline`, `followup` and `hook`. The assistant never negotiates a price, never invents a listing and
never promises a discount: those cases are detected by `intents` and handed to a person with a summary (see docs/sales-closing.md).
"""
