"""CI helper: fail the build on any un-audited or real detect-secrets finding.

Re-scans the repo against the committed `.secrets.baseline` (item 1: "add a
pre-commit secret scanner so this can't happen again"). Every finding
currently in the baseline was individually reviewed by hand and marked
`is_secret: false` -- local dev placeholders (e.g. "omniflow_dev_secret_change_me",
"clerk_managed"), test fixtures, alembic revision-id hashes, and vendored
third-party skill-doc examples, none of them real credentials.

A new commit that introduces a real secret, or any string detect-secrets
flags, shows up here as an entry with no `is_secret` key (unaudited) or
`is_secret: true` -- either one fails the build. A contributor who hits this
for a genuine false positive runs `detect-secrets scan --baseline
.secrets.baseline` locally, audits the new entry, and commits the updated
baseline -- same workflow as updating a lockfile.

Run from the repo root (unlike the bandit/pip-audit checks, which run from
omniflow-backend/), since `.secrets.baseline` covers the whole repo.
"""
import json
import subprocess
import sys

subprocess.run(
    [
        sys.executable,
        "-m",
        "detect_secrets",
        "scan",
        "--baseline",
        ".secrets.baseline",
    ],
    check=True,
)

with open(".secrets.baseline", encoding="utf-8") as f:
    baseline = json.load(f)

unresolved = [
    (filename, finding)
    for filename, findings in baseline["results"].items()
    for finding in findings
    if finding.get("is_secret") is not False
]

for filename, finding in unresolved:
    print(f"UNAUDITED {finding['type']} {filename}:{finding['line_number']}")
sys.exit(1 if unresolved else 0)
