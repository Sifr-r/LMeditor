"""Check replacement-document links, Markdown fragments and registry file:line evidence.

Line checks establish that evidence is addressable, not that it proves a claim.
Only local Markdown heading/HTML IDs are interpreted as fragments.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

REPO_ROOT = Path(__file__).resolve().parents[2]
LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


def anchors(text: str) -> set[str]:
    found = set(re.findall(r'\b(?:id|name)=["\']([^"\']+)["\']', text))
    counts: dict[str, int] = {}
    fenced = False
    for line in text.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
        if fenced:
            continue
        match = re.match(r"^#{1,6}\s+(.+?)\s*#*\s*$", line)
        if not match:
            continue
        heading = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", match[1])
        slug = re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-")
        occurrence = counts.get(slug, 0)
        counts[slug] = occurrence + 1
        found.add(f"{slug}-{occurrence}" if occurrence else slug)
    return found


def check(path: Path) -> list[str]:
    problems: list[str] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        for target in LINK.findall(line):
            target = target.strip().removeprefix("<").removesuffix(">")
            parts = urlsplit(target)
            if parts.scheme or parts.netloc:
                continue
            destination = (path.parent / unquote(parts.path)).resolve() if parts.path else path
            if not destination.exists():
                problems.append(f"{path.name}:{number}: missing target {target}")
            elif parts.fragment and destination.suffix.lower() == ".md":
                if unquote(parts.fragment) not in anchors(destination.read_text(encoding="utf-8")):
                    problems.append(f"{path.name}:{number}: missing fragment {target}")
    return problems


def check_evidence(root: Path = REPO_ROOT) -> list[str]:
    """Check registry evidence stays inside the repository and names an existing line."""
    root = root.resolve()
    registry = json.loads((root / "docs/replacement/requirements.json").read_text(encoding="utf-8"))
    problems: list[str] = []
    for entry in registry["requirements"]:
        for reference in entry["evidence"]:
            if not isinstance(reference, str):
                raise ValueError(f"{entry['id']}: evidence must be a string")
            if "::" in reference:
                continue  # Pytest node IDs are covered by their owning acceptance suite.
            filename, separator, number = reference.rpartition(":")
            path = (root / filename).resolve()
            label = f"{entry['id']}: {reference}"
            if not separator or not number.isdigit() or not path.is_relative_to(root):
                problems.append(f"{label}: malformed file:line evidence")
            elif not path.is_file():
                problems.append(f"{label}: evidence file missing")
            elif not 1 <= int(number) <= len(path.read_text(encoding="utf-8").splitlines()):
                problems.append(f"{label}: evidence line outside file")
    return problems


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(description=__doc__).parse_args(argv)
    try:
        files = sorted((REPO_ROOT / "docs/replacement").glob("*.md")) + [
            REPO_ROOT / "docs/product-replacement-roadmap.md", REPO_ROOT / "ARCHITECTURE.md"
        ]
        problems = [problem for path in files for problem in check(path)]
        problems.extend(check_evidence())
    except (OSError, ValueError, KeyError, TypeError) as error:
        problems = [f"replacement links: {error}"]
    for problem in problems:
        print(problem, file=sys.stderr)
    print(f"link/evidence problems: {len(problems)}")
    return int(bool(problems))


if __name__ == "__main__":
    raise SystemExit(main())
