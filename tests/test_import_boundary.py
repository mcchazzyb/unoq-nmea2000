"""Enforce the Layer-2 dependency rule (Requirement 21.2, Property-adjacent
architecture invariant): ``/protocol`` depends on NOTHING in ``/service``,
``/ble``, or ``/web``. Transports consume the Domain_Model, never the reverse.

This is an AST check (no third-party import-linter needed): it parses every
module under ``protocol/`` and fails if any ``import`` or ``from ... import``
names a forbidden top-level package. It is run by both ``make lint`` and the
default test suite so the boundary cannot regress silently.
"""
from __future__ import annotations

import ast
import pathlib

FORBIDDEN = {"service", "ble", "web"}
PROTOCOL_DIR = pathlib.Path(__file__).resolve().parent.parent / "protocol"


def _forbidden_imports(path: pathlib.Path) -> list[str]:
    tree = ast.parse(path.read_text(), str(path))
    bad: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top in FORBIDDEN:
                    bad.append(f"import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            # Only absolute imports can cross the boundary; a relative import
            # (level > 0) stays inside the protocol package.
            if node.level == 0 and node.module:
                top = node.module.split(".")[0]
                if top in FORBIDDEN:
                    bad.append(f"from {node.module} import ...")
    return bad


def test_protocol_imports_no_transport_layer():
    offenders: dict[str, list[str]] = {}
    for py in PROTOCOL_DIR.rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        bad = _forbidden_imports(py)
        if bad:
            offenders[str(py)] = bad
    assert not offenders, (
        "Requirement 21.2 violated — /protocol must not import /service /ble /web:\n"
        + "\n".join(f"  {f}: {imps}" for f, imps in offenders.items())
    )
