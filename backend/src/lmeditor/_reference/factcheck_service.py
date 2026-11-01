"""The frozen Python implementation of the fact-check rules.

This is the witness the drift detector measures the Kotlin tier against, so it
is **plain Python**: it never asks the Kotlin tier for an answer. It is
re-aligned to the tier's *current* semantics only when the tier's rule
deliberately changes, and every such change is recorded in the harness's
``REFERENCE_WITNESSES``. It is a copy of the implementation
``lmeditor.factcheck.service`` used to carry, with its imports rewired onto
the frozen siblings in this package so that it cannot reach the seam by
accident.

The rules it holds are the ones that make the answer worth anything:

* a claim with no evidence is ``UNGROUNDED``, never supported;
* a passage with no checkable claim is ``INCONCLUSIVE``, never supported;
* the web is consulted only when the request asks for it and a provider is
  configured, and a web failure never voids a check the corpus can answer;
* nothing is ever reported as supported without an evidence item behind it,
  and no evidence item is ever invented -- every one carries the address or
  the chunk it came from.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Final, Sequence

from lmeditor._reference.citation_checker import (
    CitationChecker,
    compute_citation_precision,
    compute_citation_recall,
    compute_groundedness_f_beta,
)
from lmeditor._reference.claim_extractor import extract_claims
from lmeditor.core.exceptions import FactCheckError, WebEvidenceError
from lmeditor.core.schemas import (
    Claim,
    ClaimVerdict,
    Evidence,
    FactCheckReport,
    FactCheckRequest,
    SourceChunk,
    VerificationScorecard,
)
from lmeditor.core.types import EntailmentStatus, EvidenceSource, FactCheckVerdict
from lmeditor._reference.factcheck_web import (
    DEFAULT_WEB_MAX_BYTES,
    DEFAULT_WEB_TIMEOUT_SECONDS,
    MAX_PAGE_TEXT_CHARS,
    WebResult,
    WebSearchProvider,
    fetch_page_text,
)
from lmeditor.verification.nli_evaluator import default_nli_evaluator

#: The retrieval index the checker may be given.
#:
#: The live ``lmeditor.provenance.service.ProvenanceIndex`` reaches the Kotlin
#: tier through the provenance lineage door, so importing it here would make
#: this witness a door itself. The index is duck-typed at runtime, and only
#: the methods the checker calls are used.
ProvenanceIndex = Any


logger = logging.getLogger(__name__)

#: Smallest number of evidence items a check may retrieve per claim.
MIN_EVIDENCE_LIMIT: Final[int] = 1

#: Largest number of evidence items a check may retrieve per claim.
MAX_EVIDENCE_LIMIT: Final[int] = 25

#: The default ``FactCheckRequest.max_evidence`` carries. A request that
#: leaves the field at this value is taken to have no preference of its own,
#: so the checker's own default applies instead.
REQUEST_EVIDENCE_DEFAULT: Final[int] = 5

#: A web snippet shorter than this carries too little text to judge, so the
#: page itself is read instead.
MIN_WEB_SNIPPET_CHARS: Final[int] = 120

#: Function words excluded from the relevance measure. A claim and a passage
#: that share only "the" share no evidence.
_STOPWORDS: Final[frozenset[str]] = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "been", "by", "for", "from",
        "had", "has", "have", "he", "her", "his", "in", "is", "it", "its", "of",
        "on", "or", "she", "that", "the", "their", "them", "there", "these",
        "they", "this", "to", "was", "were", "which", "who", "will", "with",
    }
)

#: Word tokenizer used by the relevance measure.
_TOKEN_RE: Final[re.Pattern[str]] = re.compile(r"[a-z0-9]+")

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


def _content_tokens(text: str) -> set[str]:
    """Return the words of ``text`` that carry content."""
    return {
        token
        for token in _TOKEN_RE.findall(text.lower())
        if token not in _STOPWORDS
    }


def _relevance(claim_text: str, evidence_text: str) -> float:
    """Return the share of the claim's content words the evidence contains.

    The measure is the one that decides whether a passage is *evidence at
    all*: a passage that shares nothing with the claim cannot support it,
    cannot refute it, and is not evidence for anything. Reporting such a
    claim as merely neutral would dress noise up as a finding, so the
    passage is left out and the claim is reported as ungrounded.
    """
    claim_tokens = _content_tokens(claim_text)
    if not claim_tokens:
        return 0.0
    evidence_tokens = _content_tokens(evidence_text)
    if not evidence_tokens:
        return 0.0
    shared = claim_tokens & evidence_tokens
    return round(len(shared) / len(claim_tokens), 4)


def _validate_max_evidence(max_evidence: int) -> int:
    """Return ``max_evidence`` when it is inside the accepted range.

    Raises:
        FactCheckError: the value is not an integer inside the accepted range.
    """
    if isinstance(max_evidence, bool) or not isinstance(max_evidence, int):
        raise FactCheckError(
            f"max_evidence must be an integer, got {type(max_evidence).__name__}"
        )
    if not MIN_EVIDENCE_LIMIT <= max_evidence <= MAX_EVIDENCE_LIMIT:
        raise FactCheckError(
            f"max_evidence must be between {MIN_EVIDENCE_LIMIT} and "
            f"{MAX_EVIDENCE_LIMIT}, got {max_evidence}"
        )
    return max_evidence


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

        verdict = self._aggregate(verdicts)
        scorecard = self._scorecard(claims, verdicts)

        return FactCheckReport(
            doc_id=request.doc_id,
            text=request.text,
            verdict=verdict,
            claims=verdicts,
            scorecard=scorecard,
            web_searched=web_consulted,
            summary=self._summary(
                verdicts=verdicts,
                verdict=verdict,
                evidence_found=evidence_found,
                web_consulted=web_consulted,
                notes=notes,
            ),
        )

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
        """
        if request.max_evidence != REQUEST_EVIDENCE_DEFAULT:
            return request.max_evidence
        return self._max_evidence

    # ------------------------------------------------------------------
    # Evidence
    # ------------------------------------------------------------------

    def _retrieve(self, query: str, limit: int) -> list[SourceChunk]:
        """Return the corpus passages that best answer ``query``."""
        if self._index is not None:
            ranked: list[SourceChunk] = self._index.rank_for_section(query, top_k=limit)
            return ranked

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
    # Aggregation and reporting
    # ------------------------------------------------------------------

    @staticmethod
    def _aggregate(verdicts: Sequence[ClaimVerdict]) -> FactCheckVerdict:
        """Return the verdict that covers every claim in the passage."""
        if not verdicts:
            return FactCheckVerdict.INCONCLUSIVE
        if any(item.status == EntailmentStatus.CONTRADICTED for item in verdicts):
            # A refuted claim is the finding that matters most, and it is
            # reported as such rather than diluted into "partly supported".
            return FactCheckVerdict.CONTRADICTED
        supported = sum(
            1 for item in verdicts if item.status == EntailmentStatus.ENTAILED
        )
        if supported == len(verdicts):
            return FactCheckVerdict.SUPPORTED
        if supported > 0:
            return FactCheckVerdict.PARTIALLY_SUPPORTED
        return FactCheckVerdict.UNSUPPORTED

    def _scorecard(
        self,
        claims: Sequence[Claim],
        verdicts: Sequence[ClaimVerdict],
    ) -> VerificationScorecard:
        """Return the verification scorecard for the passage.

        The counters are the ones the passage's own verdicts imply, so the
        scorecard and the per-claim verdicts can never disagree.
        """
        total = len(verdicts)
        supported = sum(1 for item in verdicts if item.status == EntailmentStatus.ENTAILED)
        contradicted = sum(
            1 for item in verdicts if item.status == EntailmentStatus.CONTRADICTED
        )
        ungrounded = sum(
            1 for item in verdicts if item.status == EntailmentStatus.UNGROUNDED
        )
        neutral = sum(1 for item in verdicts if item.status == EntailmentStatus.NEUTRAL)

        cited = sum(
            1
            for item in verdicts
            if any(e.source == EvidenceSource.CORPUS for e in item.evidence)
        )
        supported_cited = sum(
            1
            for item in verdicts
            if item.status == EntailmentStatus.ENTAILED
            and any(e.source == EvidenceSource.CORPUS for e in item.evidence)
        )

        precision = compute_citation_precision(supported_cited, cited, total)
        recall = compute_citation_recall(supported, total)
        f_beta = compute_groundedness_f_beta(precision, recall, beta=self._checker.beta)

        details = [
            Claim(
                claim_id=claim.claim_id,
                section_id=claim.section_id,
                sentence_idx=claim.sentence_idx,
                text=claim.text,
                char_start=claim.char_start,
                char_end=claim.char_end,
                cited_chunk_ids=[
                    item.chunk_id
                    for item in verdict.evidence
                    if item.source == EvidenceSource.CORPUS and item.chunk_id
                ],
                entailment_status=verdict.status,
                entailment_score=verdict.score,
                premise_text=verdict.evidence[0].excerpt if verdict.evidence else None,
                reasoning=verdict.reasoning,
            )
            for claim, verdict in zip(claims, verdicts)
        ]

        evaluator_name = getattr(self._evaluator, "evaluator_name", "")
        return VerificationScorecard(
            precision=precision,
            recall=recall,
            f_beta=f_beta,
            beta=self._checker.beta,
            total_claims=total,
            supported_claims=supported,
            ungrounded_claims=ungrounded,
            contradicted_claims=contradicted,
            neutral_claims=neutral,
            details=details,
            evaluator=str(evaluator_name) if isinstance(evaluator_name, str) else "",
        )

    @staticmethod
    def _summary(
        *,
        verdicts: Sequence[ClaimVerdict],
        verdict: FactCheckVerdict,
        evidence_found: int,
        web_consulted: bool,
        notes: Sequence[str],
    ) -> str:
        """Return the human-readable synthesis of the findings."""
        supported = sum(1 for item in verdicts if item.status == EntailmentStatus.ENTAILED)
        contradicted = sum(
            1 for item in verdicts if item.status == EntailmentStatus.CONTRADICTED
        )
        ungrounded = sum(
            1 for item in verdicts if item.status == EntailmentStatus.UNGROUNDED
        )
        neutral = sum(1 for item in verdicts if item.status == EntailmentStatus.NEUTRAL)

        sentences = [
            f"Verdict: {verdict.value}.",
            f"{supported} of {len(verdicts)} claim(s) supported by evidence, "
            f"{contradicted} contradicted, {ungrounded} ungrounded.",
        ]
        if neutral:
            sentences.append(f"{neutral} claim(s) neither supported nor refuted.")
        if evidence_found == 0:
            sentences.append(
                "No evidence was found for this passage, so nothing in it has "
                "been verified."
            )
        else:
            sentences.append(f"{evidence_found} evidence item(s) were considered.")
        if web_consulted:
            sentences.append("The open web was consulted for this check.")
        for note in notes:
            sentences.append(note)
        return " ".join(sentences)


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
