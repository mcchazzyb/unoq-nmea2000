"""Secret-scan lint step (Requirement 20.7; steering 12): fail the build if
tracked source contains a plaintext secret, credential, or private key.

ponytail: a small stdlib-only regex scanner rather than a new dependency
(e.g. detect-secrets) -- the pattern set below is deliberately narrow
(known key/token formats + generic high-entropy assignment to a
secret-sounding name) rather than a general entropy analyzer. Ceiling: it
will miss a secret in an unrecognized format or a cleverly-split string: if
that gap ever bites, the upgrade path is `detect-secrets` or `gitleaks`.

Only walks git-tracked files (via `git ls-files`) so the already-gitignored
`.env` (steering 12) and virtualenv/build artifacts are never scanned.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Known-format secrets: matched regardless of surrounding code.
_KNOWN_FORMATS = [
    (re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"), "private key block"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AWS access key ID"),
    (re.compile(r"\bghp_[A-Za-z0-9]{36}\b"), "GitHub personal access token"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"), "Slack token"),
]

# A plaintext credential assigned directly to a secret-sounding name, e.g.
#   api_key = "sk_live_abc123..."
#   PASSWORD: "..."
# Excludes an all-caps env-var-style NAME used only as a *reference* (no
# literal value), and excludes an empty/placeholder string.
_ASSIGNED_SECRET = re.compile(
    r"""(?i)\b(?:api[_-]?key|secret|password|passwd|token|private[_-]?key)\b\s*[:=]\s*
        ["']([^"'\s]{8,})["']""",
    re.VERBOSE,
)
_PLACEHOLDER = re.compile(r"(?i)^(changeme|example|placeholder|xxx+|todo|your[-_].*|<.*>|\$\{.*\}|none|null)$")

# Lines with these markers are test fixtures / documentation examples, not a
# real leaked secret -- still scanned for known-format matches, but exempt
# from the generic assigned-secret heuristic to avoid noise on, e.g., a
# documented placeholder UUID in ble/publisher.py's GATT_SCHEMA comments.
_SCAN_EXTENSIONS = {".py", ".md", ".toml", ".cfg", ".ini", ".yaml", ".yml", ".json"}


def _tracked_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    )
    return [REPO_ROOT / line for line in out.stdout.splitlines() if line]


def scan_text(text: str) -> list[tuple[int, str]]:
    """Return (line_number, reason) for every suspected secret in `text`."""
    findings: list[tuple[int, str]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for pattern, reason in _KNOWN_FORMATS:
            if pattern.search(line):
                findings.append((lineno, reason))
        for match in _ASSIGNED_SECRET.finditer(line):
            value = match.group(1)
            if not _PLACEHOLDER.match(value):
                findings.append((lineno, "plaintext credential assigned to a secret-sounding name"))
    return findings


def main() -> int:
    findings: dict[str, list[tuple[int, str]]] = {}
    for path in _tracked_files():
        if path.suffix not in _SCAN_EXTENSIONS or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        hits = scan_text(text)
        if hits:
            findings[str(path.relative_to(REPO_ROOT))] = hits

    if findings:
        print("secret-scan: possible plaintext secrets found:", file=sys.stderr)
        for file, hits in findings.items():
            for lineno, reason in hits:
                print(f"  {file}:{lineno}: {reason}", file=sys.stderr)
        return 1

    print("secret-scan OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
