"""The Kotlin tier must carry no third-party runtime dependency.

The whole point of the rewrite is that the deterministic half of LMeditor runs
on the JDK alone: JSON is hand-written, XML comes from ``javax.xml``, ZIP from
``java.util.zip``, HTTP from ``java.net.http``, hashing from ``java.security``
and dates from ``java.time``. A single ``implementation("org.example:thing:1.0")``
would quietly undo that -- the package would grow, the build would need a
repository, and the tier would stop being the thing the contract says it is.

The policy is written down in ``kotlin/build.gradle.kts`` and
``kotlin/settings.gradle.kts``, but a written-down policy is a convention, and
a convention drifts. These tests make it mechanical, in two halves:

* the **declared** dependencies of every Gradle module are read out of the
  build files and must be project-internal, or the test framework, and that
  only in a test configuration;
* the **shipped** artefact is read out of the tier jar and must contain no
  class outside LMeditor's own packages and the Kotlin language runtime.

The second half is the one that cannot be fooled: a dependency can be pulled
in transitively, or by a plugin, and neither would show up in the build
files. It needs the jar to be built, so it is **skipped** when the jar is
absent -- an absent comparison is not evidence of agreement, and a skip is
not a pass.

The rule is scoped to the **tier**. The desktop client (``:desktop``) is the
one exemption, because a user interface cannot live inside an
empty-dependency rule: it is made of dependencies. It sits above the tier
instead, and ``test_the_client_is_the_only_module_that_may_carry_dependencies``
pins that boundary so the exemption cannot spread.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
KOTLIN_ROOT = REPO_ROOT / "kotlin"
CATALOG = KOTLIN_ROOT / "gradle" / "libs.versions.toml"
TIER_JAR_DIR = KOTLIN_ROOT / "bridge" / "build" / "libs"

#: The Gradle configurations whose dependencies reach the *runtime* classpath.
#: A third-party coordinate in one of these is the violation the tier exists to
#: avoid.
RUNTIME_CONFIGURATIONS = frozenset(
    {
        "api",
        "implementation",
        "compileOnly",
        "compileOnlyApi",
        "runtimeOnly",
        "runtimeOnlyApi",
        "developmentOnly",
    }
)

#: The configurations that only ever affect the test classpath. The test
#: framework is allowed here and nowhere else.
TEST_CONFIGURATIONS = frozenset(
    {
        "testApi",
        "testCompileOnly",
        "testImplementation",
        "testRuntimeOnly",
    }
)

#: The packages the tier's own classes live in.
TIER_PACKAGES = ("lmeditor/",)

#: The packages the Kotlin language runtime and the annotations it ships with
#: live in. The standard library is the language's own runtime rather than a
#: dependency of the application: it is what the compiler brings with it, and
#: it is bundled into the tier jar for the same reason the JDK is not.
LANGUAGE_RUNTIME_PACKAGES = ("kotlin/", "org/jetbrains/", "org/intellij/")

#: The archive entries that carry the jar's own metadata rather than tier
#: content: the manifest, the Maven descriptors and the directory entries the
#: packager writes. They are not shipped data and the rule below is not about
#: them.
JAR_METADATA_PREFIXES = ("META-INF/",)

#: The data files a tier module is allowed to ship, under the one asset
#: exception in ``kotlin/CONTRACTS.md`` §1.2. Each is a typographic metric
#: table -- a versioned table of glyph advance widths -- and each travels with
#: the licence notice that governs it.
#:
#: The list is empty until a metric table is added. That is the point: the jar
#: carries no tier data file today, and the first one that arrives has to be
#: approved here before it ships. Without this rule a data file could land in
#: the tier jar unnoticed, because the class check below only looks at
#: ``.class`` entries.
APPROVED_TIER_ASSETS: frozenset[str] = frozenset()

#: The name suffix that marks a file as a typographic metric table.
METRIC_ASSET_SUFFIX = ".metrics"

#: A dependency declaration: a configuration name, then a call. The body is
#: matched loosely and classified afterwards, because the bodies are simple
#: but their shapes differ (``project(":core")`` against ``kotlin("test")``).
_DECLARATION = re.compile(
    r"""^\s*(?:"(?P<quoted>[A-Za-z][A-Za-z0-9]*)"|(?P<plain>[A-Za-z][A-Za-z0-9]*))"""
    r"""\s*\((?P<body>.+)\)\s*$""",
    re.MULTILINE,
)

#: A quoted string inside a declaration body.
_QUOTED = re.compile(r"""["']([^"']*)["']""")

#: The one module of the repository that is not a tier module.
#:
#: The desktop client is a user interface, and a user interface is made of
#: dependencies -- it cannot live inside the rule this file enforces. It sits
#: *above* the tier instead: it depends downward on ``:core``, and the tier
#: still depends on nothing at all. The empty-dependency rule is scoped to the
#: tier, and this is the one exemption.
CLIENT_MODULE = "desktop"


def _modules() -> list[Path]:
    """Every Gradle module of the *tier*, in name order.

    The desktop client is not one of them: see ``CLIENT_MODULE``.
    """
    return sorted(
        path.parent
        for path in KOTLIN_ROOT.glob("*/build.gradle.kts")
        if path.parent.name != CLIENT_MODULE
    )


def _declarations(build_file: Path) -> list[tuple[str, str]]:
    """The dependency declarations of a build file, as (configuration, body)."""
    text = build_file.read_text(encoding="utf-8")
    found: list[tuple[str, str]] = []
    for match in _DECLARATION.finditer(text):
        configuration = match.group("quoted") or match.group("plain")
        if configuration not in RUNTIME_CONFIGURATIONS | TEST_CONFIGURATIONS:
            # Not a dependency declaration: ``apply(plugin = ...)`` and the
            # like are caught by the configuration-name filter.
            continue
        found.append((configuration, match.group("body").strip()))
    return found


def _classify(body: str) -> tuple[str, str]:
    """Return (kind, detail) for a dependency body.

    The kinds are ``project`` (a reference to another module of this build),
    ``test-framework`` (the Kotlin test framework), ``catalog`` (a version
    catalog entry) and ``third-party`` (anything else).
    """
    if body.startswith("project("):
        quoted = _QUOTED.search(body)
        return "project", quoted.group(1) if quoted else body
    if body.startswith("kotlin("):
        quoted = _QUOTED.search(body)
        return "test-framework", quoted.group(1) if quoted else body
    if body.startswith("libs."):
        return "catalog", body
    return "third-party", body


@pytest.mark.parametrize("module", _modules(), ids=lambda path: path.name)
def test_a_module_declares_no_third_party_runtime_dependency(module: Path) -> None:
    """A module's runtime dependencies are all references to other modules."""
    build_file = module / "build.gradle.kts"
    offenders: list[str] = []
    for configuration, body in _declarations(build_file):
        kind, detail = _classify(body)
        if kind == "project":
            continue
        if kind == "test-framework":
            if configuration not in TEST_CONFIGURATIONS:
                offenders.append(
                    f"{configuration}({body}) -- the test framework belongs on the test classpath"
                )
            continue
        if kind == "catalog":
            offenders.append(f"{configuration}({body}) -- a version catalog library")
            continue
        offenders.append(f"{configuration}({body})")
    assert not offenders, (
        f"{module.name} declares a third-party dependency: " + "; ".join(offenders)
    )


def test_the_root_build_declares_only_the_test_framework() -> None:
    """The root build adds the test framework to every module, and nothing else."""
    root_build = KOTLIN_ROOT / "build.gradle.kts"
    offenders: list[str] = []
    for configuration, body in _declarations(root_build):
        kind, detail = _classify(body)
        if kind == "test-framework":
            if configuration not in TEST_CONFIGURATIONS:
                offenders.append(f"{configuration}({body})")
            continue
        if kind == "project":
            continue
        offenders.append(f"{configuration}({body})")
    assert not offenders, (
        "the root build declares a dependency that is not the test framework: "
        + "; ".join(offenders)
    )


def test_the_version_catalog_holds_no_libraries() -> None:
    """The version catalog names the Kotlin plugin and no library."""
    text = CATALOG.read_text(encoding="utf-8")
    assert "[libraries]" not in text, (
        "the version catalog has a [libraries] section; the tier takes no "
        "third-party runtime dependency, so a library entry there is unused "
        "at best and a dependency at worst"
    )


def test_the_client_is_the_only_module_that_may_carry_dependencies() -> None:
    """The empty-dependency rule is scoped to the tier, and the client is the one exemption.

    The desktop client is a user interface, and a user interface is made of
    dependencies. What must not happen is the exemption spreading: no tier
    module may depend on the client, because a rule that lives in the tier and
    is used by the client must not be able to depend on the client in turn.
    """
    client_build = KOTLIN_ROOT / CLIENT_MODULE / "build.gradle.kts"
    assert client_build.exists(), (
        f"the client module {CLIENT_MODULE!r} is missing; if it was renamed, "
        "the exemption in this file has to be renamed with it"
    )

    for module in _modules():
        text = (module / "build.gradle.kts").read_text(encoding="utf-8")
        assert f'project(":{CLIENT_MODULE}")' not in text, (
            f"{module.name} depends on the desktop client; the tier must not "
            "know about the user interface"
        )

    client_text = client_build.read_text(encoding="utf-8")
    assert 'project(":core")' in client_text, (
        "the client must read the wire vocabulary from the tier rather than "
        "keep a second copy of it"
    )


def _tier_jar() -> Path | None:
    """The built tier jar, if there is one."""
    if not TIER_JAR_DIR.is_dir():
        return None
    jars = sorted(TIER_JAR_DIR.glob("*-tier.jar"))
    return jars[-1] if jars else None


def test_the_shipped_tier_jar_carries_no_third_party_class() -> None:
    """The jar the Python tier runs contains no class from a third party."""
    jar = _tier_jar()
    if jar is None:
        pytest.skip(
            "the tier jar is not built; run `gradle -p kotlin :bridge:tierJar` "
            "to compare the shipped artefact"
        )
    allowed = TIER_PACKAGES + LANGUAGE_RUNTIME_PACKAGES
    offenders: list[str] = []
    with zipfile.ZipFile(jar) as archive:
        for name in archive.namelist():
            if not name.endswith(".class"):
                continue
            if not name.startswith(allowed):
                offenders.append(name)
    assert not offenders, (
        f"{jar.name} carries classes from outside the tier and the Kotlin "
        f"runtime: " + ", ".join(sorted(offenders)[:20])
    )


def test_the_shipped_tier_jar_carries_no_unapproved_data_file() -> None:
    """The jar carries no data file that the tier did not approve.

    The class check above only reads ``.class`` entries, so a shipped data file
    would pass it unnoticed. ``kotlin/CONTRACTS.md`` §1.2 admits exactly one
    kind of non-code file to the tier -- a typographic metric table, with the
    licence notice that governs it -- and this test is the half of the
    enforcement that holds that line.

    A metric table is data, not a dependency: it never appears in
    ``build.gradle.kts``, so the dependency rule above cannot see it. This
    test is what sees it.
    """
    jar = _tier_jar()
    if jar is None:
        pytest.skip(
            "the tier jar is not built; run `gradle -p kotlin :bridge:tierJar` "
            "to compare the shipped artefact"
        )
    approved = APPROVED_TIER_ASSETS
    offenders: list[str] = []
    with zipfile.ZipFile(jar) as archive:
        for name in archive.namelist():
            if name.endswith("/"):
                continue
            if name.endswith(".class"):
                continue
            if name.startswith(JAR_METADATA_PREFIXES):
                continue
            if name.startswith(LANGUAGE_RUNTIME_PACKAGES):
                continue
            if name in approved:
                continue
            offenders.append(name)
    assert not offenders, (
        f"{jar.name} carries data files that the tier has not approved. "
        f"A tier module may ship only a typographic metric table, and only "
        f"when it is listed in APPROVED_TIER_ASSETS with its licence header: "
        + ", ".join(sorted(offenders)[:20])
    )


def test_every_approved_metric_asset_carries_its_licence_header() -> None:
    """An approved metric asset names the font, licence, tool and profile.

    A metric table whose header does not say where it came from cannot be
    audited, and a licence that does not travel with the data cannot be
    honoured. The header is the record, so it is checked rather than trusted.
    """
    for name in sorted(APPROVED_TIER_ASSETS):
        assert name.endswith(METRIC_ASSET_SUFFIX), (
            f"{name} is approved as a tier asset but is not a metric table; "
            f"a metric table is named {METRIC_ASSET_SUFFIX}"
        )
        asset = KOTLIN_ROOT / "word" / "src" / "main" / "resources" / name
        assert asset.is_file(), f"{name} is approved but {asset} does not exist"
        header = asset.read_text(encoding="utf-8").splitlines()[:20]
        joined = "\n".join(header).lower()
        for field in ("font", "licence", "tool", "profile"):
            assert field in joined, (
                f"{name} does not name its {field} in its header; the header "
                f"must record the font it was derived from, the licence that "
                f"governs it, the tool and version that produced it, and the "
                f"layout profile version it belongs to"
            )
