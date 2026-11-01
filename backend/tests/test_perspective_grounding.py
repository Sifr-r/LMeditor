"""The discovery layer must describe the corpus it was given.

Audit finding 5.4: the perspectives were template personas that never saw a
source. The context is now handed to the generator, and these tests pin the
opposite behaviour -- the personas are derived from the corpus, and the
derivation follows the corpus rather than a fixed archetype table.

Two defects these tests were written against are worth naming, because both
were invisible to a contract test and only showed up when the real payload
was read:

* The corpus context prefixed every passage with its 64-character SHA-256
  digest. The digest was a token of the corpus, the salience ranking prefers
  long candidates, and the personas came out reading the material "through
  0616538a08c6484a84a0414e08e31946990ec3b93abc31f0cc7779ed0109ab74".
  That description is handed to the drafter as the persona it must adopt.
* The finding-verb lexicon carried only some inflections, so a corpus of
  plain present-tense sentences produced no findings at all and the personas
  were anchored on vocabulary but never on a claim.
"""

from __future__ import annotations

import re

from lmeditor.core.schemas import SourceChunk
from lmeditor.discovery.perspective import StormPerspectiveGenerator
from lmeditor.discovery.source_analysis import (
    content_tokens,
    extract_findings,
    salient_terms,
)
from lmeditor.workflow.nodes import perspective_discovery_node

#: Two corpora whose content words are disjoint, so a persona grounded in one
#: cannot be mistaken for a persona grounded in the other.
COMPOST_SENTENCES = (
    "Municipal composting diverts organic refuse from landfill.",
    "Windrow composting reaches thermophilic temperatures within days.",
    "Contamination of feedstock lowers the value of the finished compost.",
)

REEF_SENTENCES = (
    "Coral bleaching reduces polyp density.",
    "Ocean acidity dissolves calcium carbonate skeletons.",
    "Seagrass meadows buffer coastal wave energy.",
)

COMPOST_CORPUS = "\n\n".join(COMPOST_SENTENCES)
REEF_CORPUS = "\n\n".join(REEF_SENTENCES)

#: The same topic for both corpora. Keeping the topic corpus-neutral means
#: that any corpus-specific word in a persona can only have come from the
#: context, never from the subject line.
NEUTRAL_TOPIC = "A policy review"


def _mentions(text: str, term: str) -> bool:
    """Whether ``text`` contains ``term`` as a whole word."""
    return re.search(rf"\b{re.escape(term.lower())}\b", text.lower()) is not None


def _mentions_any(text: str, terms: set[str]) -> bool:
    return any(_mentions(text, term) for term in terms)


def _persona_text(perspectives: list[object]) -> str:
    """Everything a persona says, as one string."""
    parts: list[str] = []
    for perspective in perspectives:
        parts.append(str(perspective.description))
        parts.extend(str(item) for item in perspective.focus_areas)
        parts.extend(str(item) for item in perspective.guiding_questions)
        parts.extend(str(item) for item in perspective.key_inquiries)
    return "\n".join(parts)


def _archetype_vocabulary() -> set[str]:
    """The words the fixed role archetypes contribute, whatever the corpus.

    A corpus word that is also an archetype word proves nothing about
    grounding, so the discriminability checks subtract this vocabulary.
    """
    words: set[str] = set()
    for _role, role_name, description, focus, questions in StormPerspectiveGenerator.ROLE_ARCHETYPES:
        for text in (role_name, description, *focus, *questions):
            words.update(content_tokens(text.replace("{topic}", " ")))
    return words


def _build_chunks(sentences: tuple[str, ...], document_id: str) -> dict[str, SourceChunk]:
    """The chunks an ingestion of ``sentences`` would produce."""
    chunks: dict[str, SourceChunk] = {}
    for index, sentence in enumerate(sentences):
        byte_start = len("".join(sentences[:index]).encode("utf-8"))
        chunk = SourceChunk.create(
            document_id=document_id,
            text=sentence,
            char_start=sum(len(s) + 1 for s in sentences[:index]),
            char_end=sum(len(s) + 1 for s in sentences[:index]) + len(sentence),
            byte_start=byte_start,
            byte_end=byte_start + len(sentence.encode("utf-8")),
        )
        chunks[chunk.chunk_id] = chunk
    return chunks


def test_the_persona_reads_the_vocabulary_of_its_corpus() -> None:
    """A persona is anchored on the words the material actually uses."""
    perspectives = StormPerspectiveGenerator().generate_perspectives(
        topic=NEUTRAL_TOPIC,
        context=COMPOST_CORPUS,
        max_perspectives=4,
    )
    assert len(perspectives) == 4

    text = _persona_text(perspectives)
    for term in ("composting", "contamination", "thermophilic", "feedstock"):
        assert _mentions(text, term), f"the personas never mention {term!r}"


def test_a_finding_of_the_corpus_reaches_the_persona() -> None:
    """The persona interrogates a claim the corpus actually states."""
    perspectives = StormPerspectiveGenerator().generate_perspectives(
        topic=NEUTRAL_TOPIC,
        context=COMPOST_CORPUS,
        max_perspectives=4,
    )

    findings = extract_findings(COMPOST_CORPUS)
    assert findings, "the corpus states a finding and none was detected"
    finding = findings[0]

    text = _persona_text(perspectives)
    assert finding in text, "no persona quotes the corpus's finding"
    assert any(_mentions(area, "Interrogating the claim") for area in
               (f for p in perspectives for f in p.focus_areas)), (
        "no persona interrogates a claim"
    )


def test_the_personas_follow_the_corpus_rather_than_a_fixed_table() -> None:
    """Two corpora produce two different sets of grounded personas."""
    generator = StormPerspectiveGenerator()
    compost = generator.generate_perspectives(
        topic=NEUTRAL_TOPIC, context=COMPOST_CORPUS, max_perspectives=4
    )
    reef = generator.generate_perspectives(
        topic=NEUTRAL_TOPIC, context=REEF_CORPUS, max_perspectives=4
    )

    archetypes = _archetype_vocabulary()
    compost_only = set(content_tokens(COMPOST_CORPUS)) - set(content_tokens(REEF_CORPUS)) - archetypes
    reef_only = set(content_tokens(REEF_CORPUS)) - set(content_tokens(COMPOST_CORPUS)) - archetypes

    # Precondition: the two corpora are discriminable at all.
    assert compost_only and reef_only

    compost_text = _persona_text(compost)
    reef_text = _persona_text(reef)

    assert _mentions_any(compost_text, compost_only), (
        "the personas did not pick up the vocabulary of their corpus"
    )
    assert _mentions_any(reef_text, reef_only), (
        "the personas did not pick up the vocabulary of their corpus"
    )
    assert not _mentions_any(compost_text, reef_only), (
        "the personas borrowed vocabulary from a corpus they never saw"
    )
    assert not _mentions_any(reef_text, compost_only), (
        "the personas borrowed vocabulary from a corpus they never saw"
    )


def test_the_workflow_node_hands_the_corpus_to_the_generator() -> None:
    """The node's personas are grounded in the chunks the node was given."""
    chunks = _build_chunks(COMPOST_SENTENCES, "doc_grounding")
    result = perspective_discovery_node(
        {"topic": NEUTRAL_TOPIC, "source_chunks": chunks}
    )

    perspectives = list(result["perspectives"])
    assert len(perspectives) == 4
    assert result["status"] == "perspectives_discovered"

    text = _persona_text(perspectives)
    assert _mentions(text, "composting"), "the personas are not grounded in the corpus"

    # A finding of the corpus reaches the persona through the node.
    assert extract_findings(COMPOST_CORPUS)[0] in text


def test_no_passage_identifier_reaches_a_persona() -> None:
    """A chunk digest is an identifier, never a word of the corpus.

    The persona description is handed to the drafter as the persona it must
    adopt, so an identifier in it is an identifier in the prompt.
    """
    chunks = _build_chunks(COMPOST_SENTENCES, "doc_identifiers")
    result = perspective_discovery_node(
        {"topic": NEUTRAL_TOPIC, "source_chunks": chunks}
    )

    text = _persona_text(list(result["perspectives"]))
    for chunk_id in chunks:
        assert chunk_id not in text, "a chunk digest reached the persona"
    assert "#" not in text, "a passage marker reached the persona"


def test_an_identifier_is_never_the_most_distinctive_word() -> None:
    """A digest in the text cannot become the corpus's signature term."""
    digest = "0616538a08c6484a84a0414e08e31946990ec3b93abc31f0cc7779ed0109ab74"
    terms = salient_terms(f"[#{digest}] {COMPOST_CORPUS}", limit=10)

    assert digest not in terms
    assert terms, "the corpus still has vocabulary"
    assert "composting" in terms


def test_a_present_tense_finding_is_recognised() -> None:
    """Findings are detected in the tense the source is written in."""
    cases = [
        ("The programme reduces emissions.", True),
        ("The programme reduced emissions.", True),
        ("The programme reduces emissions by 40 per cent.", True),
        ("The programme handles 40 tonnes a year.", True),
        ("Windrow composting reaches thermophilic temperatures.", True),
        ("The programme is pleasant.", False),
        ("A short note about the garden.", False),
    ]
    for sentence, expected in cases:
        detected = extract_findings(sentence)
        assert bool(detected) is expected, (
            f"{sentence!r} was {'detected' if detected else 'missed'}"
        )
