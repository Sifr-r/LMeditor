"""Fact-checking a single passage against the corpus, the web, or both.

The rest of LMeditor verifies a *generated section* against the sources it
was grounded in. This module answers a different question: *is this one
paragraph -- or this passage somebody pasted in -- true?*

    checker = FactChecker(index=index, web=DuckDuckGoSearchProvider())
    report = await checker.check(
        FactCheckRequest(doc_id="doc", text="Coral reefs shelter a quarter of all marine species.")
    )

This module is a **door** to the Kotlin tier. The report that comes back --
the verdict over the passage, the scorecard its verdicts imply and the
summary the reader reads -- is a rule, and the rule is the Kotlin tier's. What
stays here is the part that cannot be moved: the retrieval from the corpus,
the search of the open web, and the judgement of each claim against its
evidence, because the entailment model is the Python tier's.

The rules the implementation holds itself to are the ones that make the
answer worth anything:

* a claim with no evidence is ``UNGROUNDED``, never supported;
* a passage with no checkable claim is ``INCONCLUSIVE``, never supported;
* the web is consulted only when the request asks for it and a provider is
  configured, and a web failure never voids a check the corpus can answer;
* nothing is ever reported as supported without an evidence item behind it,
  and no evidence item is ever invented -- every one carries the address or
  the chunk it came from.

There is no fallback to a Python implementation of the report rules: a silent
fallback would hide exactly the drift the Kotlin tier exists to remove.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Final, Sequence

from lmeditor.core.exceptions import FactCheckError, WebEvidenceError
from lmeditor.core.schemas import (
    ClaimVerdict,
    Evidence,
    FactCheckReport,
    FactCheckRequest,
    SourceChunk,
)
from lmeditor.core.types import EntailmentStatus, EvidenceSource, FactCheckVerdict
from lmeditor.factcheck.web import (
    DEFAULT_WEB_MAX_BYTES,
    DEFAULT_WEB_TIMEOUT_SECONDS,
    MAX_PAGE_TEXT_CHARS,
    WebResult,
    WebSearchProvider,
    fetch_page_text,
)
from lmeditor.provenance.service import ProvenanceIndex
from lmeditor.tier import factcheck as tier_factcheck
from lmeditor.verification.citation_checker import CitationChecker
from lmeditor.verification.claim_extractor import extract_claims
from lmeditor.verification.nli_evaluator import default_nli_evaluator

logger = logging.getLogger(__name__)

#: Smallest number of evidence items a check may retrieve per claim.
MIN_EVIDENCE_LIMIT: Final[int] = 1

#: Largest number of evidence items a check may retrieve per claim.
MAX_EVIDENCE_LIMIT: Final[int] = 25

#: A web snippet shorter than this carries too little text to judge, so the
#: page itself is read instead.
MIN_WEB_SNIPPET_CHARS: Final[int] = 120

#: How the verdicts are ordered when two of them score exactly the same.
#: A contradiction is the more serious reading of an ambiguous signal, so it
#: wins the tie: reporting a passage as supported when the evidence could
#: just as well refute it is the worse mistake.
_TIE_BREAK_PRIORITY: Final[dict[EntailmentStatus, int]] = {
    EntailmentStatus.CONTRADICTED: 3,
    EntailmentStatus.ENTAILED: 2,
    EntailmentStatus.NEUTRAL: 1,
    EntailmentStatus.UNGROUNDED: 0,
}


def _relevance(claim_text: str, evidence_text: str) -> float:
    """Return the share of the claim's content words the evidence contains.

    The measure is the one that decides whether a passage is *evidence at
    all*: a passage that shares nothing with the claim cannot support it,
    cannot refute it, and is not evidence for anything. Reporting such a
    claim as merely neutral would dress noise up as a finding, so the
    passage is left out and the claim is reported as ungrounded.

    **The rule is the tier's.** This is the door to it, and it holds no rule
    of its own.
    """
    return tier_factcheck.relevance(claim_text, evidence_text)


def _validate_max_evidence(max_evidence: int) -> int:
    """Return ``max_evidence`` when it is inside the accepted range.

    The range is the tier's too: a value outside it is refused by the Kotlin
    rule, and the refusal arrives as the Python ``FactCheckError``.

    Raises:
        FactCheckError: the value is not an integer inside the accepted range.
    """
    if isinstance(max_evidence, bool) or not isinstance(max_evidence, int):
        raise FactCheckError(
            f"max_evidence must be an integer, got {type(max_evidence).__name__}"
        )
    return tier_factcheck.validate_max_evidence(max_evidence)


class FactChecker:
    """Fact-checks a passage against a document's sources, the web, or both.

    The checker owns three collaborators, each of which may be replaced:

    * ``index`` -- the retrieval view of the document's corpus. When it is
      absent, the ``chunks`` passed to the constructor are ranked directly.
    * ``web`` -- the search provider. When it is absent, a check is
      corpus-only and never touches the network.
    * ``checker`` -- the citation checker that owns the verification
      metrics, and ``evaluator`` -- the NLI evaluator that judges a claim
      against its evidence. The two are linked: an injected evaluator is
      handed to the citation checker, and an injected citation checker's
      evaluator is used for the judging.

    Args:
        index: The retrieval view of the corpus, when one exists.
        chunks: The corpus itself, used when there is no index.
        web: The web-search provider, when the web may be consulted.
        checker: The citation checker that owns the metrics.
        evaluator: The NLI evaluator that judges claims.
        max_evidence: How many evidence items to retrieve per claim when the
            request does not say.

    Raises:
        FactCheckError: ``max_evidence`` is outside the accepted range.
    """

    def __init__(
        self,
        *,
        index: ProvenanceIndex | None = None,
        chunks: Sequence[SourceChunk] = (),
        web: WebSearchProvider | None = None,
        checker: CitationChecker | None = None,
        evaluator: Any | None = None,
        max_evidence: int = 5,
    ) -> None:
        """Store the collaborators the checker will use."""
        self._index = index
        self._chunks: tuple[SourceChunk, ...] = tuple(chunks)
        self._web = web
        self._max_evidence = _validate_max_evidence(max_evidence)

        if evaluator is not None:
            self._evaluator = evaluator
            self._checker = checker if checker is not None else CitationChecker(evaluator=evaluator)
        elif checker is not None:
            self._evaluator = checker.evaluator
            self._checker = checker
        else:
            self._evaluator = default_nli_evaluator
            self._checker = CitationChecker(evaluator=default_nli_evaluator)

    @property
    def max_evidence(self) -> int:
        """How many evidence items are retrieved per claim by default."""
        return self._max_evidence

    @property
    def evaluator(self) -> Any:
        """The NLI evaluator that judges claims against their evidence."""
        return self._evaluator

    async def check(self, request: FactCheckRequest) -> FactCheckReport:
        """Fact-check the passage carried by ``request``.

        The passage is split into atomic claims, each claim is given the
        evidence that speaks to it, and each claim is judged against that
        evidence. The report states, per claim and in aggregate, what the
        evidence actually supports.

        Args:
            request: The passage to check and the sources to check it against.

        Returns:
            The report for the passage. A passage with no checkable claim
            yields an ``INCONCLUSIVE`` report with no claims; a passage with
            no evidence at all yields an ``UNSUPPORTED`` report whose claims
            are ``UNGROUNDED``.

        Raises:
            FactCheckError: the request is not a usable fact-check request.
            WebEvidenceError: the web failed for every claim *and* the corpus
                could not answer the check either.
        """
        self._validate_request(request)

        claims = extract_claims(request.text, section_id=request.section_id)
        if not claims:
            return FactCheckReport(
                doc_id=request.doc_id,
                text=request.text,
                verdict=FactCheckVerdict.INCONCLUSIVE,
                claims=[],
                scorecard=None,
                web_searched=False,
                summary=(
                    "The passage contains no checkable claim, so there was "
                    "nothing to verify. It was not assessed for support."
                ),
            )

        limit = self._evidence_limit(request)
        verdicts: list[ClaimVerdict] = []
        notes: list[str] = []
        web_consulted = False
        web_failures = 0
        evidence_found = 0

        for claim in claims:
            corpus_evidence = self._corpus_evidence(claim.text, limit)
            web_evidence: list[Evidence] = []
            claim_notes: list[str] = []

            if request.include_web and self._web is not None:
                web_consulted = True
                hits, failed = self._search_web(claim.text, limit, claim_notes)
                if failed:
                    web_failures += 1
                web_evidence = self._web_evidence(claim.text, hits, claim_notes)

            evidence = corpus_evidence + web_evidence
            evidence_found += len(evidence)
            status, score, reasoning = await self._judge(claim.text, evidence)
            if claim_notes:
                reasoning = f"{reasoning} {' '.join(claim_notes)}".strip()
                notes.extend(claim_notes)

            verdicts.append(
                ClaimVerdict(
                    claim_id=claim.claim_id,
                    claim_text=claim.text,
                    status=status,
                    score=score,
                    evidence=evidence,
                    reasoning=reasoning,
                )
            )

        if (
            request.include_web
            and self._web is not None
            and web_failures == len(claims)
            and evidence_found == 0
        ):
            # Every claim failed on the web and the corpus had nothing to say
            # either: there is no evidence behind this report at all, and
            # saying so quietly would look like a verdict on the passage.
            raise WebEvidenceError(
                reason="the web provider failed for every claim and the corpus held no evidence",
                query=request.text,
            )

        # The report is the Kotlin tier's answer: the verdict over the
        # passage, the scorecard its verdicts imply and the summary the reader
        # reads are all rules, and the rules live in one place.
        report, _scorecard = tier_factcheck.aggregate(
            request.doc_id,
            request.text,
            [claim.model_dump(mode="json") for claim in claims],
            [verdict.model_dump(mode="json") for verdict in verdicts],
            web_searched=web_consulted,
            evidence_found=evidence_found,
            notes=notes,
            beta=self._checker.beta,
            evaluator_name=self._evaluator_name(),
        )
        return report

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_request(request: FactCheckRequest) -> None:
        """Refuse a request that cannot be checked.

        Raises:
            FactCheckError: the request is malformed.
        """
        if not isinstance(request, FactCheckRequest):
            raise FactCheckError(
                f"a fact-check request must be a FactCheckRequest, got "
                f"{type(request).__name__}"
            )
        if not request.doc_id.strip():
            raise FactCheckError(
                "a fact-check request must name the document whose sources are checked"
            )
        if not request.text.strip():
            raise FactCheckError("a fact-check request must carry the passage to check")
        _validate_max_evidence(request.max_evidence)

    def _evidence_limit(self, request: FactCheckRequest) -> int:
        """Return how many evidence items to retrieve for each claim.

        The request is authoritative when it expresses a preference of its
        own. A request that leaves ``max_evidence`` at the schema default
        has expressed none, so the checker's own default applies.

        **The rule is the tier's.** This is the door to it, and it holds no
        rule of its own.
        """
        return tier_factcheck.evidence_limit(
            self._max_evidence,
            request.max_evidence,
        )

    # ------------------------------------------------------------------
    # Evidence
    # ------------------------------------------------------------------

    def _retrieve(self, query: str, limit: int) -> list[SourceChunk]:
        """Return the corpus passages that best answer ``query``."""
        if self._index is not None:
            return self._index.rank_for_section(query, top_k=limit)

        scored = [(_relevance(query, chunk.text), chunk) for chunk in self._chunks]
        relevant = sorted(
            (item for item in scored if item[0] > 0.0),
            key=lambda item: (-item[0], item[1].chunk_id),
        )
        return [chunk for _, chunk in relevant[:limit]]

    def _corpus_evidence(self, claim_text: str, limit: int) -> list[Evidence]:
        """Return the corpus passages that speak to ``claim_text``."""
        evidence: list[Evidence] = []
        for chunk in self._retrieve(claim_text, limit):
            relevance = _relevance(claim_text, chunk.text)
            if relevance <= 0.0:
                # A passage that shares nothing with the claim is not
                # evidence for it; keeping it would turn silence into a
                # finding.
                continue
            evidence.append(
                Evidence(
                    source=EvidenceSource.CORPUS,
                    chunk_id=chunk.chunk_id,
                    title=chunk.document_title,
                    excerpt=chunk.text,
                    score=relevance,
                )
            )
        return evidence

    def _search_web(
        self,
        claim_text: str,
        limit: int,
        notes: list[str],
    ) -> tuple[list[WebResult], bool]:
        """Return the web hits for ``claim_text`` and whether the search failed.

        A provider that fails is contained here: the failure is recorded as a
        note on the report and never raised into the check.
        """
        if self._web is None:
            return [], False
        try:
            hits = self._web.search(claim_text, limit=limit)
        except WebEvidenceError as exc:
            logger.warning("Web evidence unavailable for %r: %s", claim_text, exc.reason)
            notes.append(f"Web evidence unavailable for '{claim_text}': {exc.reason}")
            return [], True
        except Exception as exc:  # noqa: BLE001 - a provider is an injected dependency
            logger.warning("The web provider failed for %r: %s", claim_text, exc)
            notes.append(f"Web evidence unavailable for '{claim_text}': {exc}")
            return [], True
        return list(hits), False

    def _web_evidence(
        self,
        claim_text: str,
        hits: Sequence[WebResult],
        notes: list[str],
    ) -> list[Evidence]:
        """Return the web hits that speak to ``claim_text``.

        A hit whose snippet is too thin to judge is read in full: the page
        is fetched through the vetted path and its text becomes the premise.
        A page that cannot be read is recorded as a note and the hit is kept
        as it was, because a thin snippet is still better than nothing.
        """
        evidence: list[Evidence] = []
        for hit in hits:
            if not hit.url:
                continue
            text = hit.snippet.strip()
            if len(text) < MIN_WEB_SNIPPET_CHARS:
                try:
                    page_text = fetch_page_text(
                        hit.url,
                        timeout=DEFAULT_WEB_TIMEOUT_SECONDS,
                        max_bytes=DEFAULT_WEB_MAX_BYTES,
                    )
                except WebEvidenceError as exc:
                    notes.append(f"Could not read {hit.url}: {exc.reason}")
                else:
                    if page_text:
                        text = page_text
            if not text:
                continue
            relevance = _relevance(claim_text, text)
            if relevance <= 0.0:
                continue
            evidence.append(
                Evidence(
                    source=EvidenceSource.WEB,
                    url=hit.url,
                    title=hit.title,
                    excerpt=text[:MAX_PAGE_TEXT_CHARS],
                    score=relevance,
                )
            )
        return evidence

    # ------------------------------------------------------------------
    # Judging
    # ------------------------------------------------------------------

    async def _judge(
        self,
        claim_text: str,
        evidence: Sequence[Evidence],
    ) -> tuple[EntailmentStatus, float, str]:
        """Judge ``claim_text`` against ``evidence``.

        The premise is the evidence's excerpt and the hypothesis is the
        claim. The claim's verdict is the judgement of its strongest
        evidence: the highest score wins, and an exact tie is broken in
        favour of the more serious reading.
        """
        if not evidence:
            return (
                EntailmentStatus.UNGROUNDED,
                0.0,
                "No evidence was found for this claim, so it was not verified.",
            )

        best_status = EntailmentStatus.UNGROUNDED
        best_score = 0.0
        best_excerpt = ""
        failures = 0

        for item in evidence:
            try:
                status, score = await self._evaluator.evaluate(item.excerpt, claim_text)
            except Exception as exc:  # noqa: BLE001 - one bad verdict must not void the rest
                failures += 1
                logger.warning("Could not judge a claim against an evidence item: %s", exc)
                continue
            if _is_stronger(status, score, best_status, best_score):
                best_status = status
                best_score = score
                best_excerpt = item.excerpt

        if failures == len(evidence):
            return (
                EntailmentStatus.UNGROUNDED,
                0.0,
                "Evidence was found for this claim, but none of it could be judged.",
            )

        reasoning = (
            f"Judged against {len(evidence)} evidence item(s): {best_status.value} "
            f"at {best_score:.4f}."
        )
        if best_excerpt:
            reasoning = f"{reasoning} Strongest evidence: {best_excerpt[:240]}"
        return best_status, best_score, reasoning

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def _evaluator_name(self) -> str:
        """Identifier of the NLI evaluator that judged the claims.

        The name travels to the Kotlin tier with the verdicts, because the
        model is the Python side's: the tier did not produce the verdicts, it
        only counted them.
        """
        name = getattr(self._evaluator, "evaluator_name", "")
        return str(name) if isinstance(name, str) else ""


def _is_stronger(
    status: EntailmentStatus,
    score: float,
    best_status: EntailmentStatus,
    best_score: float,
) -> bool:
    """Whether a verdict should replace the best one held so far.

    The higher score wins, because the score is the confidence the evaluator
    had. An exact tie is broken in favour of the more serious reading.
    """
    if score > best_score:
        return True
    if score < best_score:
        return False
    return _TIE_BREAK_PRIORITY.get(status, 0) > _TIE_BREAK_PRIORITY.get(best_status, 0)


__all__ = [
    "MAX_EVIDENCE_LIMIT",
    "MIN_EVIDENCE_LIMIT",
    "FactChecker",
]
