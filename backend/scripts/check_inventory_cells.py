"""Check feature matrix shape and nonblank cells; standard library only."""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
INVENTORY = REPO_ROOT / "docs/replacement/feature-inventory.md"


def cells_of(line: str) -> list[str]:
    """Split unescaped Markdown pipes, accounting for escaped backslashes."""
    cells: list[str] = []
    current: list[str] = []
    escaped = False
    for character in line.strip().removeprefix("|").removesuffix("|"):
        if character == "|" and not escaped:
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(character)
        escaped = character == "\\" and not escaped
    cells.append("".join(current).strip())
    return cells


def feature_rows(text: str) -> list[tuple[int, list[str]]]:
    rows: list[tuple[int, list[str]]] = []
    in_matrix = False
    for number, line in enumerate(text.splitlines(), 1):
        if line.startswith("## "):
            in_matrix = "feature matrix" in line.lower()
        elif line.startswith("### "):
            in_matrix = False
        if not in_matrix or not line.startswith("|"):
            continue
        cells = cells_of(line)
        if cells[0] == "Feature" or all(re.fullmatch(r":?-+:?", cell) for cell in cells):
            continue
        rows.append((number, cells))
    return rows


def check(path: Path = INVENTORY) -> list[str]:
    rows = feature_rows(path.read_text(encoding="utf-8"))
    problems = [] if rows else ["inventory: no feature matrix rows found"]
    for number, cells in rows:
        if len(cells) != 7:
            problems.append(f"inventory:{number}: {len(cells)} cells; expected 7")
        elif any(not cell for cell in cells):
            problems.append(f"inventory:{number}: blank cell")
    return problems


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(description=__doc__).parse_args(argv)
    try:
        problems = check()
    except (OSError, ValueError) as error:
        problems = [f"inventory: {error}"]
    for problem in problems:
        print(problem, file=sys.stderr)
    print(f"inventory problems: {len(problems)}")
    return int(bool(problems))


if __name__ == "__main__":
    raise SystemExit(main())
