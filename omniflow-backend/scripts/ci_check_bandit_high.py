"""CI helper: fail the build only on HIGH-severity bandit findings.

item 19 baseline: 1 MEDIUM (B104, intentional 0.0.0.0 bind in a
container), rest LOW/false-positive. A LOW/MEDIUM finding must not
block the build -- only a real new HIGH finding should.

Split out of ci.yml's inline `python -c "..."` so the check has no
shell-quoting dependency at all: the original multi-line, backslash-
escaped-quote string was bash-only syntax and does not parse the same
way under PowerShell.
"""
import json
import sys

with open("bandit-report.json", encoding="utf-8") as f:
    data = json.load(f)

high = [r for r in data["results"] if r["issue_severity"] == "HIGH"]
for r in high:
    print(f"HIGH {r['test_id']} {r['filename']}:{r['line_number']} {r['issue_text']}")
sys.exit(1 if high else 0)
