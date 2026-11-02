"""Validate inventory and corpus capability vocabulary; standard library only."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from check_inventory_cells import INVENTORY, REPO_ROOT, check as check_inventory, feature_rows

MANIFEST = REPO_ROOT / "backend/tests/fixtures/compatibility/MANIFEST.json"
VOCABULARY = {"read", "display", "edit", "preserve", "export", "limited", "unsupported", "unverified", "not-applicable"}
STATUS_CELLS = ("read", "display", "edit", "preserve", "export")


def check(inventory: Path = INVENTORY, manifest: Path = MANIFEST) -> list[str]:
    problems = check_inventory(inventory)
    for number, cells in feature_rows(inventory.read_text(encoding="utf-8")):
        if len(cells) != 7:
            continue
        for column, cell in zip(STATUS_CELLS, cells[1:6], strict=True):
            value = cell.strip("*` ").lower()
            if value not in VOCABULARY:
                problems.append(f"inventory:{number}.{column}: unknown status {value!r}")
    fixtures = json.loads(manifest.read_text(encoding="utf-8"))["fixtures"]
    if not fixtures:
        problems.append("manifest: no fixtures")
    for fixture in fixtures:
        if not isinstance(fixture["expected"], dict):
            raise ValueError(f"manifest {fixture['id']}: expected must be an object")
        for column in STATUS_CELLS:
            value = fixture["expected"].get(column, "")
            if value not in VOCABULARY:
                problems.append(f"manifest {fixture['id']}.{column}: unknown status {value!r}")
    return problems


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(description=__doc__).parse_args(argv)
    try:
        problems = check()
    except (OSError, ValueError, KeyError, TypeError) as error:
        problems = [f"compatibility vocabulary: {error}"]
    for problem in problems:
        print(problem, file=sys.stderr)
    print(f"vocabulary problems: {len(problems)}")
    return int(bool(problems))


if __name__ == "__main__":
    raise SystemExit(main())
