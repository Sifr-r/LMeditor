"""The checkout rules, and the fixtures they protect.

Git decides whether a file is text by looking for NUL bytes: a file with
none is text, and when ``core.autocrlf`` is turned on -- and it is a
machine-level setting rather than a repository one, so it follows whoever
clones -- its LF line endings are rewritten to CRLF on checkout.  The
rewrite does not show up in a diff, and it changes bytes the fixtures
assert on:

* a PDF fixture carries a classic cross-reference table of absolute byte
  offsets, so a converted file points its offsets at the wrong bytes and
  loses the document information;
* a compatibility-corpus fixture carries a sha256 in ``MANIFEST.json``, so
  a converted file no longer matches its digest.

So the repository declares the rules in ``.gitattributes``, and these tests
hold those rules to the fixtures: every digest-pinned fixture has to be
immune to an EOL conversion, and the two shared TSV corpora have to be
pinned to the line endings they are written with.

A rule that is wrong is worse than no rule at all -- a rule that rewrites
a corpus changes what the corpus asserts, silently.  So the assertions
here are about what git *resolves* for a path, not about what the file
happens to say, and the line endings a fixture is written with are read
back off the disk and compared with the rule that claims them.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Final

#: The root of the repository, two levels up from this file.
REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]

#: The two files that declare the checkout rules.
GITATTRIBUTES: Final[Path] = REPO_ROOT / ".gitattributes"
GITIGNORE: Final[Path] = REPO_ROOT / ".gitignore"

#: The compatibility corpus, and the manifest that pins a digest per fixture.
COMPATIBILITY_DIR: Final[Path] = (
    REPO_ROOT / "backend" / "tests" / "fixtures" / "compatibility"
)
MANIFEST: Final[Path] = COMPATIBILITY_DIR / "MANIFEST.json"

#: The PDF fixtures: the one the backend reads and the one the Kotlin tier
#: reads as a classpath resource.  Both carry byte offsets.
PDF_FIXTURES: Final[tuple[str, ...]] = (
    "backend/tests/fixtures/minimal.pdf",
    "kotlin/pdf/src/test/resources/fixtures/minimal.pdf",
)

#: The shared TSV corpora and the line endings each of them is written with.
#:
#: ``word_whitespace_corpus.tsv`` is written with ``newline="\\n"`` by
#: ``backend/scripts/generate_word_shared_corpus.py``; the CRLF it pins is an
#: escape sequence inside a record, not a line ending of the file.
#: ``ip_policy_reference.tsv`` is written with CRLF line endings.
TSV_CORPORA: Final[tuple[tuple[str, str], ...]] = (
    ("kotlin/word/src/test/resources/fixtures/word_whitespace_corpus.tsv", "lf"),
    ("kotlin/ingest/src/test/resources/fixtures/ip_policy_reference.tsv", "crlf"),
)

#: The Gradle project cache of the Kotlin build.  It is build output, and it
#: is not in the repository.
GRADLE_CACHE: Final[str] = "kotlin/.gradle/"

#: The Gradle output directory of the Kotlin build, which the repository's
#: ``build/`` rule already covers.
GRADLE_BUILD: Final[str] = "kotlin/build/"


def _declared_rules() -> dict[str, tuple[str, ...]]:
    """The rules the root ``.gitattributes`` declares, as pattern -> attributes."""
    rules: dict[str, tuple[str, ...]] = {}
    for line in GITATTRIBUTES.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        pattern, _, attributes = stripped.partition(" ")
        rules[pattern] = tuple(attributes.split())
    return rules


def _resolved_attributes(path: str) -> dict[str, str]:
    """The attributes git resolves for a path, by git's own reckoning."""
    result = subprocess.run(
        ["git", "check-attr", "-z", "--all", "--", path],
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise AssertionError(f"git check-attr failed for {path}: {detail}")
    # The output is path, attribute, value, repeated.
    fields = result.stdout.decode("utf-8", "replace").split("\0")
    resolved: dict[str, str] = {}
    for index in range(0, len(fields) - 2, 3):
        resolved[fields[index + 1]] = fields[index + 2]
    return resolved


def _is_ignored(path: str) -> bool:
    """Whether git ignores the given path."""
    result = subprocess.run(
        ["git", "check-ignore", "-z", "--stdin"],
        cwd=REPO_ROOT,
        input=path.encode("utf-8") + b"\0",
        capture_output=True,
        check=False,
    )
    if result.returncode not in (0, 1):
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise AssertionError(f"git check-ignore failed for {path}: {detail}")
    return result.returncode == 0


def _line_endings(data: bytes) -> str:
    """The line endings a byte string uses: ``lf``, ``crlf``, ``mixed`` or ``none``."""
    crlf = data.count(b"\r\n")
    lf = data.count(b"\n") - crlf
    cr = data.count(b"\r") - crlf
    if crlf and not lf and not cr:
        return "crlf"
    if lf and not crlf and not cr:
        return "lf"
    if not crlf and not lf and not cr:
        return "none"
    return "mixed"


def _manifest_entries() -> list[dict[str, Any]]:
    """Every fixture entry the compatibility manifest declares."""
    manifest: dict[str, Any] = json.loads(MANIFEST.read_text(encoding="utf-8"))
    entries: list[dict[str, Any]] = manifest["fixtures"]
    return [dict(entry) for entry in entries]


def _digest_pinned_files() -> list[str]:
    """Every file the compatibility manifest pins a digest for, repository-relative.

    The manifest names the vault both as a tree and one note at a time, so the
    same file can be named twice; each is listed once.
    """
    pinned: list[str] = []
    for entry in _manifest_entries():
        target = COMPATIBILITY_DIR / str(entry["path"])
        if entry["kind"] == "vault":
            # A tree digest covers every file under the tree.
            pinned.extend(
                file.relative_to(REPO_ROOT).as_posix()
                for file in sorted(target.rglob("*"))
                if file.is_file()
            )
            continue
        pinned.append(target.relative_to(REPO_ROOT).as_posix())
    ordered = dict.fromkeys(pinned)
    return list(ordered)


def test_the_repository_declares_the_checkout_rules() -> None:
    """The root ``.gitattributes`` declares the rules the fixtures need."""
    assert GITATTRIBUTES.is_file(), (
        f"the repository has no {GITATTRIBUTES.name}; without it a checkout "
        "rewrites the fixtures' line endings and invalidates their byte "
        "offsets and their digests"
    )
    rules = _declared_rules()
    assert rules.get("*.pdf") == ("binary",), (
        "a PDF fixture carries absolute byte offsets, so *.pdf must be binary; "
        f"the file declares {rules.get('*.pdf')!r}"
    )
    assert rules.get("backend/tests/fixtures/compatibility/**") == ("-text",), (
        "the compatibility corpus is digest-pinned, so no file under it may "
        "be EOL-converted; the file declares "
        f"{rules.get('backend/tests/fixtures/compatibility/**')!r}"
    )
    assert rules.get("*.tsv") == ("text", "eol=lf"), (
        "the shared TSV corpora are written with LF line endings, so LF is "
        f"the default; the file declares {rules.get('*.tsv')!r}"
    )
    assert rules.get(
        "kotlin/ingest/src/test/resources/fixtures/ip_policy_reference.tsv"
    ) == ("text", "eol=crlf"), (
        "the IP policy reference table is written with CRLF line endings, so "
        "it is the one corpus that is pinned to CRLF"
    )


def test_the_pdf_fixtures_are_binary_to_git() -> None:
    """No PDF fixture is EOL-converted, whatever the machine's config says."""
    offenders: list[str] = []
    for relative in PDF_FIXTURES:
        resolved = _resolved_attributes(relative)
        if resolved.get("text") != "unset":
            offenders.append(f"{relative}: text={resolved.get('text')!r}")
    assert not offenders, (
        "a PDF fixture must be binary, or a CRLF checkout invalidates its "
        "cross-reference offsets: " + "; ".join(offenders)
    )


def test_every_digest_pinned_fixture_is_immune_to_an_eol_conversion() -> None:
    """Every fixture the manifest pins a digest for keeps its bytes."""
    offenders: list[str] = []
    for relative in _digest_pinned_files():
        resolved = _resolved_attributes(relative)
        if resolved.get("text") != "unset":
            offenders.append(f"{relative}: text={resolved.get('text')!r}")
    assert not offenders, (
        "these fixtures are pinned by a sha256 in MANIFEST.json, so an EOL "
        "conversion breaks their digest: " + "; ".join(offenders)
    )


def test_the_tsv_corpora_are_pinned_to_the_line_endings_they_are_written_with() -> None:
    """Each TSV corpus keeps the line endings it was generated with."""
    offenders: list[str] = []
    for relative, endings in TSV_CORPORA:
        resolved = _resolved_attributes(relative)
        if resolved.get("text") != "set" or resolved.get("eol") != endings:
            offenders.append(
                f"{relative}: text={resolved.get('text')!r} "
                f"eol={resolved.get('eol')!r}"
            )
            continue
        on_disk = _line_endings((REPO_ROOT / relative).read_bytes())
        if on_disk != endings:
            offenders.append(f"{relative}: the file is {on_disk}, not {endings}")
    assert not offenders, (
        "a TSV corpus has to be pinned to the line endings it is written "
        "with, or the rule and the file disagree: " + "; ".join(offenders)
    )


def test_the_kotlin_build_output_is_ignored() -> None:
    """The Gradle project cache of the Kotlin build is not committed."""
    rules = {line.strip() for line in GITIGNORE.read_text(encoding="utf-8").splitlines()}
    assert GRADLE_CACHE in rules, (
        f"the .gitignore does not list {GRADLE_CACHE!r}; the Gradle project "
        "cache is build output and must not be committed"
    )
    assert _is_ignored(f"{GRADLE_CACHE}buildOutputCleanup/outputFiles.bin"), (
        f"git does not ignore the build output under {GRADLE_CACHE!r}"
    )
    assert _is_ignored(f"{GRADLE_BUILD}gradle.properties"), (
        f"git does not ignore the build output under {GRADLE_BUILD!r}"
    )
    assert not _is_ignored("kotlin/build.gradle.kts"), (
        "the Kotlin build files themselves must not be ignored; the rule "
        "covers the build output, not the build"
    )
