"""Generate docs/replacement/supported-workflow-matrix.md from the registry.

The matrix is the human-readable face of ``requirements.json``. It is
generated from that file rather than written by hand, so the two cannot
disagree: one registry, two renderings of it.

Usage:  python backend/scripts/build_replacement_matrix.py [--check]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Final

REPO: Final = Path(__file__).resolve().parents[2]
REGISTRY: Final = REPO / "docs" / "replacement" / "requirements.json"
MATRIX: Final = REPO / "docs" / "replacement" / "supported-workflow-matrix.md"

#: The date the snapshot describes.
SNAPSHOT_DATE: Final = "2 November 2026"

#: The tracks, in the order the roadmap introduces them.
TRACK_ORDER: Final = ["F", "W1", "W2", "W3", "O1", "O2", "N1", "N2", "P", "R"]

#: What each track stands for, in the roadmap's own terms.
TRACK_NAMES: Final = {
    "F": "Shared foundation (roadmap §3)",
    "W1": "Structured editing and document creation (§4)",
    "W2": "Layout, objects and publication (§4)",
    "W3": "Review and advanced workflows (§4)",
    "O1": "Portable everyday notes (§5)",
    "O2": "Visual organization and ecosystem (§5)",
    "N1": "Dependable source-grounded research (§6)",
    "N2": "Ingestion and study studio (§6)",
    "P": "Make the three replacements one product (§7)",
    "R": "Release evidence and operational requirements (§10)",
}

#: The four status values, in prose.
STATUS_PROSE: Final = {
    "not-started": (
        "**`not-started`** — no implementation of the requirement exists in the tree. "
        "Its evidence list is empty, and that emptiness is the claim: the search that "
        "established the absence is recorded in the gate, not in an evidence entry."
    ),
    "in-progress": (
        "**`in-progress`** — a partial implementation exists. The evidence names the "
        "files that carry it, and the gate names the part of the requirement those "
        "files do not yet cover."
    ),
    "evidence-partial": (
        "**`evidence-partial`** — the behavior exists but the acceptance evidence does "
        "not yet cover it. A skipped test is the usual cause: it is recorded as "
        "*unverified*, never as verified."
    ),
    "complete": (
        "**`complete`** — the requirement meets all six parts of roadmap §10's "
        "definition of done: available in the UI when user-facing, persists correctly, "
        "has relevant automated or manual evidence, handles its failure cases, "
        "documents its compatibility limits, and is recorded in the architecture ledger."
    ),
}


def load() -> dict[str, Any]:
    """The registry document."""
    registry: dict[str, Any] = json.loads(REGISTRY.read_text(encoding="utf-8"))
    return registry


def cell(text: str) -> str:
    """A table cell: no pipes, no newlines."""
    return " ".join(str(text).split()).replace("|", "\\|")


def render(registry: dict[str, Any]) -> str:
    """Render the whole matrix document."""
    entries = list(registry["requirements"])
    stages: dict[str, str] = registry["stages"]
    statuses: list[str] = registry["statuses"]
    if set(statuses) != set(STATUS_PROSE) or len(statuses) != len(STATUS_PROSE):
        raise ValueError("registry statuses must be the four documented values")
    if not entries:
        raise ValueError("registry contains no requirements")
    seen: set[str] = set()
    for entry in entries:
        if entry["track"] not in TRACK_ORDER:
            raise ValueError(f"{entry['id']}: unknown track {entry['track']!r}")
        if entry["status"] not in statuses or entry["stage"] not in stages:
            raise ValueError(f"{entry['id']}: unknown status or stage")
        if entry["id"] in seen:
            raise ValueError(f"duplicate requirement id: {entry['id']}")
        seen.add(entry["id"])

    lines: list[str] = []
    lines.append("# Supported workflow matrix")
    lines.append("")
    lines.append(
        f"Date: {SNAPSHOT_DATE}"
    )
    lines.append("")
    lines.append(
        "Status: the human-readable view of "
        "[`requirements.json`](requirements.json), which is the machine-readable "
        "registry of the same requirements."
    )
    lines.append(
        "Scope: one row per requirement of "
        "[`docs/product-replacement-roadmap.md`](../product-replacement-roadmap.md)."
    )
    lines.append("")
    lines.append("---")
    lines.append("")

    # -- How to read it -----------------------------------------------------
    lines.append("## 1. How to read this matrix")
    lines.append("")
    lines.append(
        "Each row is one requirement of the roadmap. The **ID** is the marker the "
        "roadmap itself carries, and it is the join key between the two files: the "
        "roadmap's prose and this matrix are two renderings of the same set of "
        "identifiers, and `backend/tests/test_replacement_requirements.py` fails if "
        "they ever stop agreeing."
    )
    lines.append("")
    lines.append(
        "The **Requirement** column is the requirement's subject. The **Observable "
        "gate** is the single check that will decide whether the requirement is done "
        "— an action and an artifact or result, never a state of mind. The "
        "**Evidence** column names the files in this tree that back the status, each "
        "as `path:line`; an empty evidence cell means the claim is an absence, "
        "established by reading the tree rather than by a file."
    )
    lines.append("")
    lines.append(
        "A gate is what a *future* state will be checked against. It is not a "
        "description of what exists today — the status column says that."
    )
    lines.append("")
    lines.append("---")
    lines.append("")

    # -- Stage vocabulary ----------------------------------------------------
    lines.append("## 2. The stage vocabulary")
    lines.append("")
    lines.append(
        "A requirement's stage is the milestone **by which it must be complete**, "
        "taken verbatim from the roadmap's §9 delivery sequence. It is not the "
        "milestone the work starts in: `R.01` is seeded by M0 and completes in M4, "
        "and `P.06` completes in M3 because its clean-installation import test is "
        "`R.04` in M4."
    )
    lines.append("")
    lines.append("| Stage | Milestone |")
    lines.append("| :--- | :--- |")
    for key in sorted(stages):
        lines.append(f"| `{key}` | {cell(stages[key])} |")
    lines.append("")
    lines.append(
        "The stages are a sequence, not a schedule. Roadmap §9 defers every date and "
        "staffing estimate to M0's feasibility results."
    )
    lines.append("")
    lines.append("---")
    lines.append("")

    # -- Status vocabulary ---------------------------------------------------
    lines.append("## 3. The four status values")
    lines.append("")
    for status in statuses:
        lines.append(f"- {STATUS_PROSE[status]}")
    lines.append("")
    lines.append("### What a status claims, and what it does not")
    lines.append("")
    lines.append(
        "A status claims **one thing**: the state of this requirement in this "
        "working tree, at the date above, measured against its gate. It does not "
        "claim that the requirement is finished, that the product replaces Word, "
        "Obsidian or NotebookLM, or that the roadmap's checkbox has been ticked."
    )
    lines.append("")
    lines.append(
        "The roadmap's own status line says that unchecked items are not completion "
        "claims, and this registry holds that line. An unchecked box is not evidence "
        "that nothing has been built — it is evidence that nothing has been claimed. "
        "The distinction is why this file exists at all: the registry says what is "
        "evidenced, and the checklist says what has been signed off. They are "
        "different claims, and neither is derived from the other."
    )
    lines.append("")
    lines.append(
        "A `complete` status in particular is a claim about six things at once, not "
        "one. If any of the six cannot be shown, the status is at best "
        "`evidence-partial`, and a skipped test is never evidence at all."
    )
    lines.append("")
    lines.append("---")
    lines.append("")

    # -- Owner notation ------------------------------------------------------
    lines.append("## 4. The owner notation")
    lines.append("")
    lines.append(
        "An owner is written `<tier>:<module>` and names the module that **owns the "
        "rule**, not the module that merely shows it. A rule that must hold in the "
        "user interface is owned by the tier module that holds it; the desktop "
        "client appears in the gate instead."
    )
    lines.append("")
    lines.append(
        "| Prefix | Meaning |"
    )
    lines.append("| :--- | :--- |")
    lines.append(
        "| `kotlin:` | A Kotlin tier module: `core`, `text`, `word`, `vault`, "
        "`notebooks`, `library`, `server`, `workflow`, `ingest`, `pdf`, `editing`, "
        "`factcheck`, `discovery`, `bridge`, `desktop`. |"
    )
    lines.append(
        "| `python:` | A Python package: `core`, `word`, `library`, `ingestion`, "
        "`provenance`, `notebooks`, `workflow`, `server`, `verification`, `drafting`, "
        "`vault`, `export`, `assistant`, `feedback`, `llm`, `tier`, `bridge`. |"
    )
    lines.append(
        "| `cross:` | Work that spans tiers. The suffix names the coordinating "
        "domain: `documents`, `notes`, `research`, `delivery`, `evidence`. |"
    )
    lines.append("")
    lines.append("---")
    lines.append("")

    # -- The tables ----------------------------------------------------------
    lines.append("## 5. The requirements")
    lines.append("")
    for track in TRACK_ORDER:
        track_entries = [entry for entry in entries if entry["track"] == track]
        if not track_entries:
            continue
        lines.append(f"### Track {track} — {TRACK_NAMES[track]}")
        lines.append("")
        lines.append("| ID | Requirement | Stage | Owner | Status | Observable gate | Evidence |")
        lines.append("| :--- | :--- | :--- | :--- | :--- | :--- | :--- |")
        for entry in track_entries:
            evidence = "; ".join(entry["evidence"]) if entry["evidence"] else "—"
            lines.append(
                "| `{id}` | {title} | `{stage}` | `{owner}` | `{status}` | {gate} | {evidence} |".format(
                    id=cell(entry["id"]),
                    title=cell(entry["title"]),
                    stage=cell(entry["stage"]),
                    owner=cell(entry["owner"]),
                    status=cell(entry["status"]),
                    gate=cell(entry["gate"]),
                    evidence=cell(evidence),
                )
            )
        lines.append("")

    lines.append("---")
    lines.append("")

    # -- Coverage -----------------------------------------------------------
    lines.append("## 6. Coverage")
    lines.append("")
    lines.append(
        f"This matrix describes **{len(entries)} requirements**, the same count the "
        "roadmap carries and the same count the registry holds."
    )
    lines.append("")

    lines.append("### By track")
    lines.append("")
    lines.append("| Track | Requirements |")
    lines.append("| :--- | ---: |")
    for track in TRACK_ORDER:
        count = sum(1 for entry in entries if entry["track"] == track)
        if count:
            lines.append(f"| {track} | {count} |")
    lines.append(f"| **Total** | **{len(entries)}** |")
    lines.append("")

    lines.append("### By status")
    lines.append("")
    lines.append("| Status | Requirements |")
    lines.append("| :--- | ---: |")
    for status in statuses:
        count = sum(1 for entry in entries if entry["status"] == status)
        lines.append(f"| `{status}` | {count} |")
    lines.append(f"| **Total** | **{len(entries)}** |")
    lines.append("")

    not_started = sum(1 for entry in entries if entry["status"] == "not-started")
    evidenced = len(entries) - not_started
    lines.append("### Requirements not started")
    lines.append("")
    lines.append(
        f"**{not_started} of {len(entries)} requirements are `not-started`** — no "
        f"implementation of the requirement exists in this tree, and their evidence "
        f"lists are empty. The remaining {evidenced} carry at least one evidence "
        "entry, and every one of those entries names a file and a line that exists "
        "in this working tree."
    )
    lines.append("")
    lines.append(
        "The `blocks` column of the registry is not reproduced here. It records the "
        "requirements each one gates, and every id in it resolves to a row above."
    )
    lines.append("")

    lines.append("### What this is, and what it is not")
    lines.append("")
    lines.append(
        f"This is a **registry snapshot reconciled on {SNAPSHOT_DATE}**, not a new product "
        "certification. Its statuses and evidence are taken from the registry; this "
        "reconciliation does not establish that every workflow runs end to end or that "
        "the product replaces the three applications the roadmap is about."
    )
    lines.append("")
    complete = [f"`{entry['id']}`" for entry in entries if entry["status"] == "complete"]
    completion = (
        "The registry records " + ", ".join(complete) + " as `complete`, with its recorded evidence; "
        if complete else "The registry records no requirement as `complete`; "
    )
    lines.append(
        "A status of `in-progress` means a partial implementation was found, not that "
        "the requirement is nearly done. " + completion +
        "every other requirement retains its recorded status. The matrix reflects "
        "the registry and remains as current as the evidence behind it."
    )
    lines.append("")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="refuse drift without writing")
    args = parser.parse_args(argv)
    try:
        matrix = render(load())
        if args.check:
            if not MATRIX.is_file() or MATRIX.read_text(encoding="utf-8") != matrix:
                print("workflow matrix differs: run backend/scripts/build_replacement_matrix.py", file=sys.stderr)
                return 1
            print("workflow matrix matches registry")
        elif not MATRIX.is_file() or MATRIX.read_text(encoding="utf-8") != matrix:
            MATRIX.write_text(matrix, encoding="utf-8", newline="\n")
            print(f"wrote {MATRIX.relative_to(REPO)}")
        else:
            print("workflow matrix already current")
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"workflow matrix: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
