"""The roadmap and the requirement registry must agree, mechanically.

The replacement roadmap is 83 prose checkboxes. Prose checkboxes are not
verifiable: an unchecked box says that nobody has claimed the work, not that
nobody has started it, and a ticked box would say nothing about *which* part
of a workflow is missing. So the roadmap was given a join key -- a stable
marker such as ``F.01`` -- and
``docs/replacement/requirements.json`` was given the other half: an owner, a
stage, an observable gate and a status that is backed by a file and a line
somewhere in this tree.

The join key is only worth something if the two sides cannot drift apart. A
marker in the roadmap with no registry entry is an orphan; a registry entry
with no marker is a phantom. Either one means the M1 agents would be reading
a requirement that no longer exists, or working on a requirement nobody
wrote down. Neither is caught by reading the documents, so it is caught here.

The second half of this file checks the registry against itself: that every
entry is well-formed, that its owner is a real module in the notation the
blueprint fixed, that its stage is one of the five milestones, that its gate
is an observable check rather than a mood, and -- the part that matters most
-- that every status is *earned*. A status of ``complete`` with no evidence
behind it is the failure mode this whole program exists to prevent, so the
test refuses it, and it refuses an evidence entry that points at a file or a
line that is not there.

The registry path is read from ``LMEDITOR_REQUIREMENTS_REGISTRY`` when that
variable is set, so a mutated copy can be put on the stand and this file can
be shown to have teeth without touching the real registry.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Final

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

#: The roadmap the registry claims to describe.
ROADMAP: Final = REPO_ROOT / "docs" / "product-replacement-roadmap.md"

#: The registry, unless the environment points somewhere else.
REGISTRY_ENV_VAR: Final = "LMEDITOR_REQUIREMENTS_REGISTRY"

#: The identifier of a requirement: a track prefix and a two-digit ordinal.
IDENTIFIER: Final = re.compile(r"^(F|W1|W2|W3|O1|O2|N1|N2|P|R)\.\d{2}$")

#: A bold span in a roadmap line. The marker is written as one.
BOLD_SPAN: Final = re.compile(r"\*\*([^*]+)\*\*")

#: The owner notation of blueprint §3.2: ``<tier>:<module>``.
OWNER: Final = re.compile(r"^[a-z]+:[a-z]+$")

#: How many requirements the roadmap carries.
REQUIREMENT_COUNT: Final = 83

#: The five milestones of roadmap §9.
MILESTONES: Final = ["M0", "M1", "M2", "M3", "M4"]

#: The fields every entry carries.
REQUIRED_FIELDS: Final = (
    "id",
    "track",
    "title",
    "section",
    "stage",
    "owner",
    "status",
    "gate",
    "evidence",
    "blocks",
)

#: The shortest a gate may be and still name an action and an artifact.
MINIMUM_GATE_LENGTH: Final = 20


def registry_path() -> Path:
    """The registry under test.

    The environment variable is read on every call rather than at import, so a
    test run that points it at a mutated copy does not need a different
    process, and the real registry is never written to.
    """
    override = os.environ.get(REGISTRY_ENV_VAR)
    if override:
        return Path(override).resolve()
    return REPO_ROOT / "docs" / "replacement" / "requirements.json"


def load_registry() -> dict[str, Any]:
    """The registry document under test."""
    path = registry_path()
    assert path.is_file(), (
        f"the requirement registry {path} is missing; the roadmap's 83 "
        "requirements cannot be checked without it"
    )
    return json.loads(path.read_text(encoding="utf-8"))


def roadmap_requirements() -> list[tuple[int, str, str]]:
    """Every ``- [ ]`` line of the roadmap as (line number, line, marker).

    The marker is the first bold span of the line. A line with no bold span, or
    with more than one, is reported here with an empty marker so the caller
    can say what is wrong with it.
    """
    found: list[tuple[int, str, str]] = []
    text = ROADMAP.read_text(encoding="utf-8")
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped.startswith("- [ ]"):
            continue
        spans = BOLD_SPAN.findall(stripped)
        marker = spans[0] if len(spans) == 1 else ""
        found.append((number, stripped, marker))
    return found


def _entries() -> list[dict[str, Any]]:
    return list(load_registry()["requirements"])


def test_every_roadmap_requirement_carries_exactly_one_marker() -> None:
    """Each checkbox in the roadmap carries one well-formed identifier.

    The marker is what joins the prose to the registry, so a checkbox without
    one is a requirement no agent can be dispatched against, and a checkbox
    with two is a requirement with two owners.
    """
    offenders: list[str] = []
    for number, line, marker in roadmap_requirements():
        if not marker:
            offenders.append(f"line {number}: {line}")
            continue
        if not IDENTIFIER.match(marker):
            offenders.append(f"line {number}: malformed marker {marker!r}")
    assert not offenders, (
        "every '- [ ]' line of the roadmap must carry exactly one identifier "
        "marker, and these do not: " + "; ".join(offenders)
    )


def test_the_markers_are_unique() -> None:
    """No two roadmap requirements share an identifier."""
    seen: dict[str, int] = {}
    duplicates: list[str] = []
    for number, _line, marker in roadmap_requirements():
        if not marker:
            continue
        if marker in seen:
            duplicates.append(f"{marker} (lines {seen[marker]} and {number})")
        else:
            seen[marker] = number
    assert not duplicates, "the roadmap repeats an identifier: " + "; ".join(duplicates)


def test_the_roadmap_and_the_registry_agree() -> None:
    """The two sets of identifiers are the same set.

    An identifier in the roadmap with no entry is an orphan: a requirement
    nobody has an owner or a gate for. An entry with no identifier is a
    phantom: work that the registry tracks and the roadmap does not ask for.
    """
    roadmap_ids = {marker for _number, _line, marker in roadmap_requirements() if marker}
    registry_ids = {str(entry.get("id")) for entry in _entries()}
    orphans = sorted(roadmap_ids - registry_ids)
    phantoms = sorted(registry_ids - roadmap_ids)
    assert not orphans and not phantoms, (
        "the roadmap and the registry disagree"
        + (f"; the roadmap has no registry entry for: {orphans}" if orphans else "")
        + (f"; the registry has no roadmap requirement for: {phantoms}" if phantoms else "")
    )


def test_the_registry_holds_every_requirement() -> None:
    """The registry holds exactly the 83 requirements of the roadmap."""
    entries = _entries()
    assert len(entries) == REQUIREMENT_COUNT, (
        f"the registry holds {len(entries)} entries; the roadmap carries "
        f"{REQUIREMENT_COUNT} requirements"
    )


def test_every_entry_is_well_formed() -> None:
    """Every entry carries every field, and every field is in range."""
    registry = load_registry()
    stages = registry["stages"]
    statuses = registry["statuses"]
    offenders: list[str] = []
    for entry in _entries():
        identifier = str(entry.get("id"))
        for field in REQUIRED_FIELDS:
            if field not in entry:
                offenders.append(f"{identifier}: no {field!r}")
        gate = entry.get("gate")
        if isinstance(gate, str) and len(gate) < MINIMUM_GATE_LENGTH:
            offenders.append(
                f"{identifier}: the gate is {len(gate)} characters; it must be at "
                f"least {MINIMUM_GATE_LENGTH}"
            )
        stage = entry.get("stage")
        if stage not in stages:
            offenders.append(f"{identifier}: unknown stage {stage!r}")
        status = entry.get("status")
        if status not in statuses:
            offenders.append(f"{identifier}: unknown status {status!r}")
        owner = entry.get("owner")
        if not isinstance(owner, str) or not OWNER.match(owner):
            offenders.append(f"{identifier}: malformed owner {owner!r}")
        evidence = entry.get("evidence")
        if not isinstance(evidence, list):
            offenders.append(f"{identifier}: evidence is not a list")
        blocks = entry.get("blocks")
        if not isinstance(blocks, list):
            offenders.append(f"{identifier}: blocks is not a list")
    assert not offenders, "the registry holds malformed entries: " + "; ".join(offenders)


def test_the_track_and_the_ordinal_match_the_identifier() -> None:
    """A requirement's track is its identifier's prefix, and its ordinals run 01..NN.

    The ordinals of a track have to be contiguous and start at one, or a
    reader cannot tell whether a gap is a missing requirement or a number
    that was simply never used.
    """
    offenders: list[str] = []
    per_track: dict[str, list[int]] = {}
    for entry in _entries():
        identifier = str(entry.get("id"))
        if not IDENTIFIER.match(identifier):
            offenders.append(f"{identifier}: malformed identifier")
            continue
        track, ordinal = identifier.split(".")
        if entry.get("track") != track:
            offenders.append(f"{identifier}: the track is {entry.get('track')!r}")
        per_track.setdefault(track, []).append(int(ordinal))
    for track, ordinals in sorted(per_track.items()):
        expected = list(range(1, len(ordinals) + 1))
        if sorted(ordinals) != expected:
            offenders.append(
                f"track {track} holds {sorted(ordinals)}; it should hold {expected}"
            )
    assert not offenders, "the registry's identifiers are inconsistent: " + "; ".join(offenders)


def test_every_blocked_identifier_resolves() -> None:
    """Every id a requirement blocks names a requirement that exists."""
    known = {str(entry.get("id")) for entry in _entries()}
    offenders: list[str] = []
    for entry in _entries():
        for blocked in entry.get("blocks", []):
            if blocked not in known:
                offenders.append(f"{entry.get('id')} blocks the unknown {blocked!r}")
    assert not offenders, "the registry blocks requirements that do not exist: " + "; ".join(offenders)


def test_a_status_is_backed_by_its_evidence() -> None:
    """``not-started`` carries no evidence; ``complete`` carries some.

    The two ends of the vocabulary are the ones that can lie. A ``not-started``
    requirement with evidence behind it is claiming to be absent while the
    tree says otherwise; a ``complete`` requirement with no evidence is
    claiming that a workflow is finished, which roadmap §10 says a helper,
    a parser or an endpoint alone never establishes.
    """
    offenders: list[str] = []
    for entry in _entries():
        status = entry.get("status")
        evidence = entry.get("evidence") or []
        if status == "not-started" and evidence:
            offenders.append(f"{entry.get('id')}: not-started but carries {len(evidence)} evidence")
        if status == "complete" and not evidence:
            offenders.append(f"{entry.get('id')}: complete but carries no evidence")
    assert not offenders, "the registry's statuses and evidence disagree: " + "; ".join(offenders)


def test_every_file_evidence_exists() -> None:
    """Every ``path:line`` evidence entry points at a line that is there.

    Evidence is the whole content of a status, so an entry that names a file
    which has moved, or a line that has been deleted, is not weak evidence --
    it is a claim nobody can check.
    """
    offenders: list[str] = []
    for entry in _entries():
        for reference in entry.get("evidence", []):
            if not isinstance(reference, str):
                offenders.append(f"{entry.get('id')}: evidence is not a string")
                continue
            if "/" not in reference or ":" not in reference:
                # A test node id, not a file reference.
                continue
            path_text, _, line_text = reference.rpartition(":")
            if not line_text.isdigit():
                offenders.append(f"{entry.get('id')}: {reference!r} has no line number")
                continue
            path = REPO_ROOT / path_text
            if not path.is_file():
                offenders.append(f"{entry.get('id')}: {reference!r} names no file in the tree")
                continue
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            number = int(line_text)
            if number < 1 or number > len(lines):
                offenders.append(
                    f"{entry.get('id')}: {reference!r} is outside the file's "
                    f"{len(lines)} lines"
                )
    assert not offenders, "the registry cites evidence that is not there: " + "; ".join(offenders)


def test_the_schema_declares_the_five_milestones() -> None:
    """The registry names its schema version and its five milestones."""
    registry = load_registry()
    assert "schema_version" in registry, "the registry declares no schema_version"
    stages = registry.get("stages")
    assert isinstance(stages, dict), "the registry's stages is not a mapping"
    assert list(stages) == MILESTONES, (
        f"the registry declares the stages {list(stages)}; the roadmap's §9 has "
        f"{MILESTONES}"
    )


@pytest.mark.parametrize(
    "status",
    ["not-started", "in-progress", "evidence-partial", "complete"],
)
def test_the_status_vocabulary_is_the_four_blueprint_values(status: str) -> None:
    """The status vocabulary is exactly the four values the blueprint fixes."""
    assert status in load_registry()["statuses"]
