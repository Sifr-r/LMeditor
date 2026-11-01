"""The claim extractor, as the Kotlin tier implements it.

Sentence segmentation and proposition decomposition are rules, and the rules
are Kotlin's: they are implemented once, in ``lmeditor.text``, and the tier
serves them across the NDJSON seam.

This module is the **door**: it keeps the Python API the rest of the backend
calls, and answers with the tier's answers. The implementation it used to
carry now lives in ``lmeditor._reference.claim_extractor``, frozen, so the
parity harness can still ask both sides the same question.
"""

from __future__ import annotations

from lmeditor.bridge import BridgeUnavailableError
from lmeditor.core.exceptions import ClaimExtractionError
from lmeditor.core.schemas import Claim, InlineCitation
from lmeditor.tier import text as tier_text


def _require_text(text: str) -> None:
    """Refuse anything that is not text, the way the original did."""
    if not isinstance(text, str):
        raise ClaimExtractionError(
            f"Expected str input for text, got {type(text).__name__}"
        )


class ClaimExtractor:
    """Splits a passage into the claims it makes.

    Every method here is the tier's rule; this class exists so that the
    callers keep the API they had.
    """

    def segment_sentences(self, text: str) -> list[str]:
        """Split ``text`` into the sentences the extractor works on."""
        _require_text(text)
        try:
            return tier_text.segment_sentences(text)
        except BridgeUnavailableError:
            # The tier is not there. That is an infrastructure failure, not a
            # bad input, and it must not be dressed up as one: the caller needs
            # to know that the rule could not be answered at all.
            raise
        except ClaimExtractionError:
            raise
        except Exception as exc:  # pragma: no cover - the tier's own errors
            raise ClaimExtractionError(
                f"Error during sentence segmentation: {exc}"
            ) from exc

    def extract_citations(self, text: str) -> list[InlineCitation]:
        """Return the inline citation tokens ``text`` carries."""
        _require_text(text)
        return tier_text.extract_citations(text)

    def extract_claims(self, text: str, section_id: str = "") -> list[Claim]:
        """Split ``text`` into atomic propositions."""
        _require_text(text)
        return tier_text.extract_claims(text, section_id)


_DEFAULT_EXTRACTOR = ClaimExtractor()


def extract_claims(text: str, section_id: str = "") -> list[Claim]:
    """Split ``text`` into atomic propositions."""
    return _DEFAULT_EXTRACTOR.extract_claims(text, section_id)


def extract_citations(text: str) -> list[InlineCitation]:
    """Return the inline citation tokens ``text`` carries."""
    return _DEFAULT_EXTRACTOR.extract_citations(text)


def segment_sentences(text: str) -> list[str]:
    """Split ``text`` into the sentences the extractor works on."""
    return _DEFAULT_EXTRACTOR.segment_sentences(text)


#: The name the workflow uses for the extractor.
PropositionClaimExtractor = ClaimExtractor


__all__ = [
    "ClaimExtractor",
    "PropositionClaimExtractor",
    "extract_claims",
    "extract_citations",
    "segment_sentences",
]
