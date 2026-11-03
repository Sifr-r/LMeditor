"""Verify file and sorted-tree SHA-256 corpus digests without modifying fixtures."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

CORPUS = Path(__file__).resolve().parents[2] / "backend/tests/fixtures/compatibility"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def tree_digest(root: Path) -> str:
    lines: list[str] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if path.is_symlink():
            raise ValueError(f"corpus tree contains symlink: {path.name}")
        if path.is_file():
            lines.append(f"{path.relative_to(root).as_posix()}\t{sha(path.read_bytes())}\n")
    return sha("".join(lines).encode("utf-8"))


def check(corpus: Path = CORPUS) -> list[str]:
    corpus = corpus.resolve()
    fixtures = json.loads((corpus / "MANIFEST.json").read_text(encoding="utf-8"))["fixtures"]
    problems = [] if fixtures else ["corpus: no fixtures"]
    seen: set[str] = set()
    for fixture in fixtures:
        identifier = fixture["id"]
        if identifier in seen:
            problems.append(f"{identifier}: duplicate fixture id")
        seen.add(identifier)
        relative = Path(fixture["path"])
        path = (corpus / relative).resolve()
        if relative.is_absolute() or not path.is_relative_to(corpus) or path == corpus:
            problems.append(f"{identifier}: fixture path escapes corpus or names its root")
            continue
        if path.is_dir():
            got = tree_digest(path)
        elif path.is_file():
            got = sha(path.read_bytes())
        else:
            problems.append(f"{identifier}: path missing")
            continue
        if got != fixture["sha256"]:
            problems.append(f"{identifier}: digest mismatch")
    return problems


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(description=__doc__).parse_args(argv)
    try:
        problems = check()
    except (OSError, ValueError, KeyError, TypeError) as error:
        problems = [f"corpus digests: {error}"]
    for problem in problems:
        print(problem, file=sys.stderr)
    print(f"digest problems: {len(problems)}")
    return int(bool(problems))


if __name__ == "__main__":
    raise SystemExit(main())
