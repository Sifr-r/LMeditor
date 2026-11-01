"""The frozen deterministic lexical analysis of source material.

This is the original Python implementation, kept here so that the parity
harness has a witness to compare the Kotlin tier against. It is plain Python on
purpose: see ``lmeditor._reference``.

It is frozen. The vocabulary of a passage, the findings its sentences state,
the headings a document already carries and the way its passages cluster are
the tier's rules now: the live module ``lmeditor.discovery.source_analysis``
is the door to them, and this copy is the yardstick they are measured
against.
"""

from __future__ import annotations

import re
from collections import Counter

#: Function words excluded from the content vocabulary.
STOPWORDS: frozenset[str] = frozenset(
    {
        "the", "a", "an", "and", "or", "but", "if", "then", "than", "so", "because",
        "of", "in", "on", "at", "to", "for", "with", "by", "from", "into", "onto",
        "over", "under", "about", "after", "before", "during", "while", "as",
        "is", "are", "was", "were", "be", "been", "being", "has", "have", "had",
        "do", "does", "did", "will", "would", "can", "could", "should", "may",
        "might", "must", "shall", "that", "this", "these", "those", "it", "its",
        "they", "them", "their", "we", "us", "our", "you", "your", "he", "she",
        "his", "her", "not", "no", "never", "also", "such", "there", "here",
        "which", "who", "what", "when", "where", "how", "why", "all", "any",
        "each", "other", "more", "most", "some", "only", "own", "same", "both",
        "very", "however", "therefore", "thus", "hence", "furthermore", "moreover",
    }
)

#: Tokens that describe the act of writing rather than the subject matter.
GENERIC_TERMS: frozenset[str] = frozenset(
    {
        "study", "studies", "paper", "papers", "article", "articles", "research",
        "researcher", "researchers", "authors", "author", "data", "results",
        "result", "method", "methods", "methodology", "analysis", "analyses",
        "evidence", "findings", "finding", "conclusion", "conclusions", "report",
        "reports", "section", "sections", "document", "documents", "text",
        "passage", "passages", "source", "sources", "example", "examples",
        "figure", "figures", "table", "tables", "introduction", "background",
        "overview", "summary", "abstract", "discussion", "appendix", "chapter",
        "paper's", "study's",
    }
)

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9'\-]*")

#: Verb families that mark a sentence as a statement of a finding.
#:
#: Each family lists the forms a writer actually uses. Listing the families
#: rather than a flat bag of words is what keeps the lexicon honest: a family
#: that carried only its past tense (``reduced``) silently ignored the
#: present tense (``reduce``, ``reduces``) that most source prose is written
#: in, so a corpus of plain present-tense sentences produced no findings at
#: all and the discovery layer fell back to bare vocabulary.
_FINDING_VERB_FAMILIES: tuple[tuple[str, ...], ...] = (
    # Verbs of discovery, assertion and measurement.
    ("show", "shows", "showed", "shown"),
    ("find", "finds", "found"),
    ("reveal", "reveals", "revealed"),
    ("demonstrate", "demonstrates", "demonstrated"),
    ("report", "reports", "reported"),
    ("observe", "observes", "observed"),
    ("measure", "measures", "measured"),
    ("record", "records", "recorded"),
    ("identify", "identifies", "identified"),
    ("document", "documents", "documented"),
    ("conclude", "concludes", "concluded"),
    ("estimate", "estimates", "estimated"),
    ("confirm", "confirms", "confirmed"),
    ("indicate", "indicates", "indicated"),
    ("suggest", "suggests", "suggested"),
    ("establish", "establishes", "established"),
    ("yield", "yields", "yielded"),
    ("result", "results", "resulted"),
    # Verbs of change and direction.
    ("reduce", "reduces", "reduced"),
    ("increase", "increases", "increased"),
    ("fall", "falls", "fell"),
    ("rise", "rises", "rose"),
    ("grow", "grows", "grew"),
    ("decline", "declines", "declined"),
    ("improve", "improves", "improved"),
    ("worsen", "worsens", "worsened"),
    ("deteriorate", "deteriorates", "deteriorated"),
    ("remain", "remains", "remained"),
    ("double", "doubles", "doubled"),
    ("halve", "halves", "halved"),
    ("triple", "triples", "tripled"),
    ("exceed", "exceeds", "exceeded"),
    ("drop", "drops", "dropped"),
    ("climb", "climbs", "climbed"),
    ("peak", "peaks", "peaked"),
    ("lower", "lowers", "lowered"),
    ("raise", "raises", "raised"),
    ("reach", "reaches", "reached"),
    ("expand", "expands", "expanded"),
    ("contract", "contracts", "contracted"),
    ("accelerate", "accelerates", "accelerated"),
    ("delay", "delays", "delayed"),
    ("prevent", "prevents", "prevented"),
    ("cause", "causes", "caused"),
    ("produce", "produces", "produced"),
    ("achieve", "achieves", "achieved"),
    ("maintain", "maintains", "maintained"),
    ("shift", "shifts", "shifted"),
)

#: Every inflection of every finding verb family.
_FINDING_VERBS: frozenset[str] = frozenset(
    form for family in _FINDING_VERB_FAMILIES for form in family
)

#: The finding verbs as a single word-boundary pattern. Matching whole words
#: rather than substrings keeps "find" from matching inside "findings" and
#: "report" from matching inside "reported" for reasons that have nothing to
#: do with the word actually being a verb.
_FINDING_VERB_RE: re.Pattern[str] = re.compile(
    r"\b(?:" + "|".join(sorted(_FINDING_VERBS, key=len, reverse=True)) + r")\b"
)

#: Heading forms recognised in a source document.
_HEADING_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^#{1,6}\s+(?P<title>.+?)\s*$"),
    re.compile(r"^(?P<title>(?:[IVXLC]+|\d+)[.)]\s+\S.*?)\s*$"),
    re.compile(r"^(?:chapter|section|part)\s+(?P<title>\S.*?)\s*$", re.IGNORECASE),
    re.compile(r"^(?P<title>[A-Z][A-Z0-9 ,'\-]{6,})$"),
)


#: Longest token treated as a word rather than as an identifier.
#:
#: A chunk digest, a UUID or a long URL fragment is a token like any other,
#: and the salience ranking prefers long candidates, so an identifier left in
#: the text would be reported as the most distinctive word of the corpus.
_MAX_WORD_LENGTH = 24


def tokenize(text: str) -> list[str]:
    """Lower-cased word tokens of ``text``."""
    return _TOKEN_RE.findall(text.lower())


def content_tokens(text: str) -> list[str]:
    """Tokens of ``text`` that carry content, in order of appearance.

    Identifiers are excluded: they are not vocabulary, and the discovery
    layer reads this list to decide what a corpus is about.
    """
    return [
        token
        for token in tokenize(text)
        if token not in STOPWORDS
        and len(token) > 2
        and not token.isdigit()
        and len(token) <= _MAX_WORD_LENGTH
    ]


def salient_terms(text: str, limit: int = 12) -> list[str]:
    """The most distinctive vocabulary of ``text``.

    Terms are scored by frequency and ties are broken towards the longer,
    more specific word. Tokens too long to be words never enter the ranking
    at all, because a reader never names a passage by its digest.
    """
    tokens = content_tokens(text)
    if not tokens:
        return []
    counts = Counter(tokens)
    ranked = sorted(
        counts.items(),
        key=lambda item: (-item[1], -len(item[0]), item[0]),
    )
    return [term for term, _ in ranked[:limit]]


def salient_terms_across(texts: list[str], limit: int = 12) -> list[str]:
    """Salient terms of several passages at once.

    A term that occurs in every passage is demoted: it is part of the shared
    vocabulary of the corpus rather than a finding of any one passage.
    """
    if not texts:
        return []
    counts: Counter[str] = Counter()
    document_frequency: Counter[str] = Counter()
    for text in texts:
        tokens = set(content_tokens(text))
        counts.update(tokens)
        document_frequency.update(tokens)
    total = len(texts)
    scored = sorted(
        counts.items(),
        key=lambda item: (
            -(item[1] * (1.0 + (total - document_frequency[item[0]]) / total)),
            item[0],
        ),
    )
    return [term for term, _ in scored[:limit]]


def split_sentences(text: str) -> list[str]:
    """Split ``text`` into sentences on terminal punctuation."""
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'\[])", text.strip())
    return [part.strip() for part in parts if part.strip()]


def extract_findings(text: str, limit: int = 6) -> list[str]:
    """The sentences of ``text`` that state a finding.

    A finding sentence is one that carries a quantity or an explicit finding
    verb -- those are the sentences a source-grounded persona can interrogate.
    """
    findings: list[str] = []
    for sentence in split_sentences(text):
        lowered = sentence.lower()
        has_number = bool(re.search(r"\d", lowered))
        has_verb = _FINDING_VERB_RE.search(lowered) is not None
        if has_number or has_verb:
            findings.append(sentence.strip())
        if len(findings) >= limit:
            break
    return findings


def detect_headings(text: str) -> list[str]:
    """Headings that the document itself carries.

    Only line-initial heading forms are recognised, so a sentence that merely
    starts with a capital letter is not mistaken for a heading.
    """
    headings: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        for pattern in _HEADING_PATTERNS:
            match = pattern.match(line)
            if match is None:
                continue
            title = match.group("title").strip()
            if 3 <= len(title) <= 120:
                headings.append(title)
            break
    # Preserve order while removing duplicates.
    seen: set[str] = set()
    unique: list[str] = []
    for heading in headings:
        key = heading.lower()
        if key not in seen:
            seen.add(key)
            unique.append(heading)
    return unique


def title_case_terms(terms: list[str]) -> str:
    """Join ``terms`` into a readable section heading fragment."""
    return " & ".join(term.capitalize() for term in terms)


def _jaccard(left: set[str], right: set[str]) -> float:
    """Jaccard similarity of two token sets."""
    if not left or not right:
        return 0.0
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)


def cluster_by_terms(texts: list[str], num_clusters: int) -> list[list[int]]:
    """Group the indices of ``texts`` into topical clusters.

    The clustering is agglomerative and fully deterministic: the two most
    similar clusters are merged until the requested number of clusters
    remains. When no two clusters share any vocabulary the two smallest
    clusters are merged instead, because a corpus that is genuinely
    heterogeneous still has to be reported as a hierarchy.
    """
    count = len(texts)
    if count == 0:
        return []
    target = max(1, min(num_clusters, count))

    token_sets = [set(content_tokens(text)) for text in texts]
    clusters: list[list[int]] = [[index] for index in range(count)]

    while len(clusters) > target:
        best_similarity = -1.0
        best_pair: tuple[int, int] = (0, 1)
        for a in range(len(clusters)):
            for b in range(a + 1, len(clusters)):
                similarity = _jaccard(
                    set().union(*(token_sets[index] for index in clusters[a])),
                    set().union(*(token_sets[index] for index in clusters[b])),
                )
                if similarity > best_similarity:
                    best_similarity = similarity
                    best_pair = (a, b)

        if best_similarity > 0.0:
            left, right = best_pair
            merged = clusters[left] + clusters[right]
            clusters = [
                cluster for index, cluster in enumerate(clusters) if index not in (left, right)
            ] + [merged]
            continue

        # Nothing is related any more: merge the two smallest clusters so the
        # hierarchy still has the requested shape.
        order = sorted(range(len(clusters)), key=lambda index: (len(clusters[index]), index))
        left, right = order[0], order[1]
        merged = clusters[left] + clusters[right]
        clusters = [
            cluster for index, cluster in enumerate(clusters) if index not in (left, right)
        ] + [merged]

    return clusters


__all__ = [
    "GENERIC_TERMS",
    "STOPWORDS",
    "cluster_by_terms",
    "content_tokens",
    "detect_headings",
    "extract_findings",
    "salient_terms",
    "salient_terms_across",
    "split_sentences",
    "title_case_terms",
    "tokenize",
]
