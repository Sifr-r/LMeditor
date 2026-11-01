"""Fact-checking a passage against the corpus, the web, or both.

The point of this domain is that a passage is never reported as supported
without evidence behind it, and that no source is ever invented. These tests
pin both halves of that promise: the verdicts a corpus-backed passage gets,
and the way a web failure is contained rather than allowed to void a check
the corpus can answer.

No test in this file touches the network. The web provider is either a
recorded fake or the real provider, whose rules -- the request it makes, the
response it parses -- are the Kotlin tier's and are tested there.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from typing import Any

import pytest

from lmeditor.core.exceptions import FactCheckError, WebEvidenceError
from lmeditor.core.schemas import FactCheckRequest, SourceChunk
from lmeditor.core.types import EntailmentStatus, EvidenceSource, FactCheckVerdict
from lmeditor.factcheck import (
    DuckDuckGoSearchProvider,
    FactChecker,
    NullWebSearchProvider,
    WebResult,
)
from lmeditor.factcheck import web as factcheck_web
from lmeditor._reference import factcheck_web as factcheck_web_reference
from lmeditor.provenance.service import ProvenanceIndex
from lmeditor.verification.nli_evaluator import HeuristicNLIEvaluator

REEF_SENTENCE = "Coral reefs shelter a quarter of all marine species and their larvae."
PHOTOSYNTHESIS_SENTENCE = (
    "Photosynthesis converts light energy into chemical energy stored in glucose."
)
REVENUE_SENTENCE = "Company revenue rose by 30 percent in 2024."

#: A snippet long enough that the page is never fetched for it.
WEB_SNIPPET = (
    "Coral reefs shelter a quarter of all marine species and their larvae, "
    "according to the global reef census published in 2024, which surveyed "
    "more than four hundred reef sites across the tropics."
)


def _chunk(text: str, document_id: str = "doc") -> SourceChunk:
    """Return a chunk holding ``text``."""
    return SourceChunk.create(
        document_id=document_id,
        text=text,
        char_start=0,
        char_end=len(text),
        byte_start=0,
        byte_end=len(text.encode("utf-8")),
    )


def _corpus() -> list[SourceChunk]:
    """Return the corpus the checks are run against."""
    return [
        _chunk(REEF_SENTENCE),
        _chunk("Mangrove forests slow storm surge and trap sediment along tropical coasts."),
        _chunk("Medieval abbeys kept meticulous accounts of grain harvests and rents."),
        _chunk(REVENUE_SENTENCE),
    ]


class _RecordingWebProvider:
    """A web provider that answers from a fixed list, or fails on demand."""

    def __init__(
        self,
        results: list[WebResult] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.results = list(results or [])
        self.error = error
        self.queries: list[str] = []

    def search(self, query: str, limit: int = 5) -> list[WebResult]:
        """Return the canned results, or raise the canned failure."""
        self.queries.append(query)
        if self.error is not None:
            raise self.error
        return list(self.results[:limit])


def _checker(
    *,
    chunks: list[SourceChunk] | None = None,
    web: Any | None = None,
    max_evidence: int = 5,
) -> FactChecker:
    """Return a checker that judges with the deterministic heuristic."""
    return FactChecker(
        chunks=chunks if chunks is not None else _corpus(),
        web=web,
        evaluator=HeuristicNLIEvaluator(),
        max_evidence=max_evidence,
    )


# ============================================================================
# 1. A passage the corpus states
# ============================================================================


@pytest.mark.asyncio
async def test_a_supported_passage_is_supported_and_never_consults_the_web() -> None:
    """A claim the corpus states outright is supported, without a web call."""
    web = _RecordingWebProvider()
    checker = _checker(web=web)

    report = await checker.check(FactCheckRequest(doc_id="doc", text=REEF_SENTENCE))

    assert report.verdict == FactCheckVerdict.SUPPORTED
    assert report.web_searched is False
    assert web.queries == []
    assert len(report.claims) == 1
    assert report.claims[0].status == EntailmentStatus.ENTAILED
    assert report.claims[0].evidence
    assert report.claims[0].evidence[0].source == EvidenceSource.CORPUS
    assert report.claims[0].evidence[0].chunk_id
    assert report.scorecard is not None
    assert report.scorecard.supported_claims == 1


# ============================================================================
# 2. A passage the corpus does not state
# ============================================================================


@pytest.mark.asyncio
async def test_a_passage_without_supporting_evidence_is_unsupported_and_ungrounded() -> None:
    """A claim no source speaks to is ungrounded, and the passage unsupported."""
    checker = _checker()

    report = await checker.check(
        FactCheckRequest(doc_id="doc", text=PHOTOSYNTHESIS_SENTENCE)
    )

    assert report.verdict == FactCheckVerdict.UNSUPPORTED
    assert report.verdict != FactCheckVerdict.SUPPORTED
    assert report.claims
    for claim in report.claims:
        assert claim.status == EntailmentStatus.UNGROUNDED
        assert claim.score == 0.0
        assert claim.evidence == []
    assert "No evidence was found" in report.summary


# ============================================================================
# 3. A passage with nothing checkable in it
# ============================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["...", "[1]"])
async def test_a_passage_without_checkable_claims_is_inconclusive(text: str) -> None:
    """A passage with no proposition in it is inconclusive, never supported."""
    checker = _checker()

    report = await checker.check(FactCheckRequest(doc_id="doc", text=text))

    assert report.verdict == FactCheckVerdict.INCONCLUSIVE
    assert report.verdict != FactCheckVerdict.SUPPORTED
    assert report.claims == []


# ============================================================================
# 4. A passage that is only partly stated by the corpus
# ============================================================================


@pytest.mark.asyncio
async def test_a_mixed_passage_is_partially_supported() -> None:
    """One stated claim and one ungrounded claim make the passage partial."""
    checker = _checker()

    report = await checker.check(
        FactCheckRequest(doc_id="doc", text=f"{REEF_SENTENCE} {PHOTOSYNTHESIS_SENTENCE}")
    )

    assert report.verdict == FactCheckVerdict.PARTIALLY_SUPPORTED
    statuses = {claim.status for claim in report.claims}
    assert EntailmentStatus.ENTAILED in statuses
    assert EntailmentStatus.UNGROUNDED in statuses


# ============================================================================
# 5. A passage the corpus refutes
# ============================================================================


@pytest.mark.asyncio
async def test_a_contradicted_claim_yields_a_contradicted_verdict() -> None:
    """A claim the corpus refutes is reported as contradicted, not supported."""
    checker = _checker()

    report = await checker.check(
        FactCheckRequest(doc_id="doc", text="Company revenue fell by 30 percent in 2024.")
    )

    assert report.verdict == FactCheckVerdict.CONTRADICTED
    assert report.claims[0].status == EntailmentStatus.CONTRADICTED
    assert report.claims[0].evidence
    assert report.scorecard is not None
    assert report.scorecard.contradicted_claims == 1


# ============================================================================
# 6. Web evidence
# ============================================================================


@pytest.mark.asyncio
async def test_web_evidence_is_recorded_when_the_web_is_consulted() -> None:
    """A web hit becomes an evidence item, and the report says it was used."""
    web = _RecordingWebProvider(
        results=[
            WebResult(
                url="https://reefs.example.org/census",
                title="Global reef census",
                snippet=WEB_SNIPPET,
            )
        ]
    )
    checker = _checker(chunks=[], web=web)

    report = await checker.check(
        FactCheckRequest(doc_id="doc", text=REEF_SENTENCE, include_web=True)
    )

    assert report.web_searched is True
    assert web.queries == [REEF_SENTENCE.rstrip(".")]
    sources = {
        evidence.source for claim in report.claims for evidence in claim.evidence
    }
    assert EvidenceSource.WEB in sources
    web_evidence = [
        evidence
        for claim in report.claims
        for evidence in claim.evidence
        if evidence.source == EvidenceSource.WEB
    ]
    assert web_evidence[0].url == "https://reefs.example.org/census"
    assert web_evidence[0].title == "Global reef census"


# ============================================================================
# 7. A web failure is contained
# ============================================================================


@pytest.mark.asyncio
async def test_a_web_failure_does_not_void_a_corpus_backed_check() -> None:
    """A web that fails is reported; the corpus half of the check still stands."""
    web = _RecordingWebProvider(
        error=WebEvidenceError(
            reason="the search endpoint refused the request",
            query=REEF_SENTENCE,
        )
    )
    checker = _checker(web=web)

    report = await checker.check(
        FactCheckRequest(doc_id="doc", text=REEF_SENTENCE, include_web=True)
    )

    assert report.verdict == FactCheckVerdict.SUPPORTED
    assert report.claims[0].status == EntailmentStatus.ENTAILED
    # The failure is reported rather than swallowed.
    assert "the search endpoint refused the request" in report.summary
    assert "the search endpoint refused the request" in report.claims[0].reasoning


@pytest.mark.asyncio
async def test_a_web_failure_with_no_evidence_at_all_is_raised() -> None:
    """When nothing could be found anywhere, the caller is told so."""
    web = _RecordingWebProvider(
        error=WebEvidenceError(reason="the search endpoint refused the request")
    )
    checker = _checker(web=web)

    with pytest.raises(WebEvidenceError):
        await checker.check(
            FactCheckRequest(
                doc_id="doc",
                text=PHOTOSYNTHESIS_SENTENCE,
                include_web=True,
            )
        )


# ============================================================================
# 8. The null provider
# ============================================================================


@pytest.mark.asyncio
async def test_the_null_web_provider_returns_nothing_and_a_corpus_check_never_calls_the_web() -> None:
    """Without web access the check is corpus-only, and says so."""
    assert NullWebSearchProvider().search("coral reefs") == []

    web = _RecordingWebProvider()
    checker = _checker(web=web)

    report = await checker.check(FactCheckRequest(doc_id="doc", text=REEF_SENTENCE))

    assert web.queries == []
    assert report.web_searched is False
    assert report.verdict == FactCheckVerdict.SUPPORTED


# ============================================================================
# 9. The web seam
# ============================================================================
#
# The rules behind the web seam -- the address the request goes to, the query
# it carries, the parsing of the response, the address a result points at and
# the text a page is read as -- are the Kotlin tier's now, and they are tested
# there. What is left here is the door: the API the callers already use, and
# the answers the tier sends back through it.


def test_the_web_door_keeps_the_public_api_of_the_module_it_replaced() -> None:
    """The door is the module it replaced, name for name and shape for shape.

    The comparison is made on the source rather than on the imported objects,
    because a door must keep the *signatures* it was given and not merely the
    names: a default that moved, a keyword that became positional or a return
    annotation that changed are all drifts the door must not have.
    """
    assert _public_api(factcheck_web.__file__) == _public_api(
        factcheck_web_reference.__file__,
    )


def test_the_web_seam_keeps_the_constants_the_callers_import() -> None:
    """The numbers the callers import are the numbers the module had."""
    assert factcheck_web.DUCKDUCKGO_HOST == "html.duckduckgo.com"
    assert factcheck_web.DUCKDUCKGO_SEARCH_PATH == "/html/"
    assert factcheck_web.DUCKDUCKGO_SEARCH_BASE == "https://html.duckduckgo.com/html/"
    assert factcheck_web.DEFAULT_WEB_TIMEOUT_SECONDS == 8.0
    assert factcheck_web.DEFAULT_WEB_MAX_BYTES == 262_144
    assert factcheck_web.MAX_PAGE_TEXT_CHARS == 4_000


def test_the_duckduckgo_provider_keeps_its_budget() -> None:
    """The provider still carries the timeout and the body cap it was given."""
    provider = DuckDuckGoSearchProvider()

    assert provider.timeout == 8.0
    assert provider.max_bytes == 262_144

    patient = DuckDuckGoSearchProvider(timeout=2.5, max_bytes=1024)

    assert patient.timeout == 2.5
    assert patient.max_bytes == 1024


def test_the_duckduckgo_provider_refuses_an_empty_query() -> None:
    """An empty query is a typed error, not an empty result."""
    with pytest.raises(WebEvidenceError):
        DuckDuckGoSearchProvider().search("   ")


def test_the_result_list_is_parsed_into_results_with_their_addresses() -> None:
    """A well-formed result list is parsed into results with their addresses."""
    results = factcheck_web.parse_search_results(
        b"""<html><body>
        <div class="result results_links web-result">
          <h2 class="result__title">
            <a rel="nofollow" class="result__a"
               href="//duckduckgo.com/l/?uddg=https%3A%2F%2Freefs.example.org%2Fcensus&amp;rut=abc">Global reef census</a>
          </h2>
          <a class="result__snippet"
             href="//duckduckgo.com/l/?uddg=https%3A%2F%2Freefs.example.org%2Fcensus">Coral reefs shelter a quarter of all marine species.</a>
        </div>
        </body></html>""",
    )

    assert len(results) == 1
    assert results[0].url == "https://reefs.example.org/census"
    assert results[0].title == "Global reef census"
    assert "quarter" in results[0].snippet


def test_a_result_address_is_normalised_to_the_address_it_points_at() -> None:
    """The redirect the engine wrapped a result in is unwrapped."""
    assert (
        factcheck_web.normalise_result_url("//reefs.example.org/census")
        == "https://reefs.example.org/census"
    )


def test_the_page_text_is_the_visible_text_of_the_document() -> None:
    """The page is read as its visible text."""
    text = factcheck_web.extract_page_text(
        b"<html><body><h1>Reef census</h1><p>Coral reefs shelter.</p></body></html>",
    )

    assert text == "Reef census Coral reefs shelter."


def _public_api(path: str) -> list[str]:
    """The module-defined public API of the file at ``path``.

    One line per name, in the order the names appear. The lines carry the
    signature, the decorators, the defaults and the annotations, so a change
    to any of them shows up as a change to the API.
    """
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    lines: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            if not node.name.startswith("_"):
                lines.append(f"def {_signature(node)}")
        elif isinstance(node, ast.ClassDef):
            if node.name.startswith("_"):
                continue
            bases = ", ".join(ast.unparse(base) for base in node.bases)
            lines.append(f"class {node.name}({bases}){_decorators(node)}")
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and not item.name.startswith("_"):
                    lines.append(f"    {_decorators(item)}def {_signature(item)}")
                elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                    field = f"    {item.target.id}: {ast.unparse(item.annotation)}"
                    if item.value is not None:
                        field += f" = {ast.unparse(item.value)}"
                    lines.append(field)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if not node.target.id.startswith("_"):
                value = ast.unparse(node.value) if node.value is not None else ""
                lines.append(f"{node.target.id}: {ast.unparse(node.annotation)} = {value}")
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and (
                    not target.id.startswith("_") or target.id == "__all__"
                ):
                    lines.append(f"{target.id} = {ast.unparse(node.value)}")
    return lines


def _decorators(node: ast.FunctionDef | ast.ClassDef) -> str:
    """The decorators of ``node``, as they are written."""
    if not node.decorator_list:
        return ""
    return "@" + ", @".join(ast.unparse(item) for item in node.decorator_list) + " "


def _signature(node: ast.FunctionDef) -> str:
    """The signature of ``node``, as it is written."""
    args = node.args
    parts: list[str] = []
    defaults: list[ast.expr | None] = [None] * (len(args.args) - len(args.defaults))
    defaults.extend(args.defaults)
    for arg, default in zip(args.args, defaults):
        parts.append(_argument(arg, default))
    if args.vararg is not None:
        parts.append(f"*{args.vararg.arg}")
    elif args.kwonlyargs:
        parts.append("*")
    for arg, default in zip(args.kwonlyargs, args.kw_defaults):
        parts.append(_argument(arg, default))
    if args.kwarg is not None:
        parts.append(f"**{args.kwarg.arg}")
    returns = f" -> {ast.unparse(node.returns)}" if node.returns is not None else ""
    return f"{node.name}({', '.join(parts)}){returns}"


def _argument(arg: ast.arg, default: ast.expr | None) -> str:
    """One argument of a signature, with its annotation and its default."""
    text = arg.arg
    if arg.annotation is not None:
        text += f": {ast.unparse(arg.annotation)}"
    if default is not None:
        text += f" = {ast.unparse(default)}"
    return text


# ============================================================================
# 10. The retrieval path and the boundary
# ============================================================================


@pytest.mark.asyncio
async def test_the_corpus_index_is_used_for_retrieval() -> None:
    """The index the checker is given is the corpus it searches."""
    index = ProvenanceIndex.build(_corpus())
    checker = FactChecker(index=index, evaluator=HeuristicNLIEvaluator())

    report = await checker.check(FactCheckRequest(doc_id="doc", text=REEF_SENTENCE))

    assert report.verdict == FactCheckVerdict.SUPPORTED
    assert report.claims[0].evidence[0].source == EvidenceSource.CORPUS
    assert report.claims[0].evidence[0].chunk_id


@pytest.mark.asyncio
async def test_the_checker_keeps_its_own_evidence_limit() -> None:
    """A checker configured with fewer evidence items retrieves fewer."""
    web = _RecordingWebProvider(
        results=[
            WebResult(url=f"https://example.org/{index}", snippet=WEB_SNIPPET)
            for index in range(5)
        ]
    )
    checker = _checker(chunks=[], web=web, max_evidence=2)

    report = await checker.check(
        FactCheckRequest(doc_id="doc", text=REEF_SENTENCE, include_web=True)
    )

    assert len(report.claims[0].evidence) == 2


@pytest.mark.asyncio
async def test_an_unusable_request_is_refused() -> None:
    """A request that was not validated by the schema is refused at the boundary."""
    checker = _checker()

    # ``model_construct`` skips the schema's own validation, so the checker
    # has to refuse the request itself rather than trust the field values.
    broken = FactCheckRequest.model_construct(doc_id="", text=REEF_SENTENCE)

    with pytest.raises(FactCheckError):
        await checker.check(broken)

    out_of_range = FactCheckRequest.model_construct(
        doc_id="doc",
        text=REEF_SENTENCE,
        max_evidence=0,
    )
    with pytest.raises(FactCheckError):
        await checker.check(out_of_range)


def test_an_out_of_range_evidence_limit_is_refused() -> None:
    """A limit outside the accepted range is refused when the checker is built."""
    with pytest.raises(FactCheckError):
        FactChecker(max_evidence=0)
    with pytest.raises(FactCheckError):
        FactChecker(max_evidence=99)


def test_the_check_is_awaitable_from_a_plain_script() -> None:
    """The public entry point is a coroutine the caller can await."""
    checker = _checker()

    report = asyncio.run(
        checker.check(FactCheckRequest(doc_id="doc", text=REEF_SENTENCE))
    )

    assert report.verdict == FactCheckVerdict.SUPPORTED
    assert report.doc_id == "doc"
    assert report.text == REEF_SENTENCE
