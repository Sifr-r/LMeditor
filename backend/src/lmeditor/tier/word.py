"""The OOXML rules, as the Kotlin tier implements them.

The document model, the reader and the writer are Kotlin's. A document
crossing the seam is a plain JSON object, because the two tiers describe a
``.docx`` in their own model classes; the conversion between them belongs to
the tier that owns the model.

The contract is ``kotlin/word/WIRE.md``: it pins the wire form of every model
type (§2) and the request and the response of the three seam methods this
module calls (§3).  This module is the thin call across the seam, plus the
decoding of the answer into the Python tier's own models.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, NamedTuple

from lmeditor.bridge.client import decode_bytes, encode_bytes
from lmeditor.core.exceptions import ValidationError
from lmeditor.core.schemas import BibliographicRecord, DocumentDraft
from lmeditor.tier.bridge import tier_bridge
from lmeditor.word.model import WordDocument, WordPartRelationship


class WordReadResult(NamedTuple):
    """What a read of a ``.docx`` package produced.

    Attributes:
        document: The document the tier read.
        omissions: Everything the reader could not preserve, in the order the
            reader recorded it.
        package_relationships: The relationships the package root holds,
            sorted by ``relationship_id``.
    """

    document: WordDocument
    omissions: list[str]
    package_relationships: tuple[WordPartRelationship, ...]


class WordWriteResult(NamedTuple):
    """What a write of a document produced.

    Attributes:
        data: The bytes of the package.
        omissions: Everything the writer could not represent, in the order the
            writer recorded it.
    """

    data: bytes
    omissions: list[str]


def read_document(
    data: bytes,
    *,
    base_uri: str | None = None,
    origin: str | None = None,
) -> WordReadResult:
    """Parse a ``.docx`` package into the document model.

    The tier never touches the filesystem on Python's behalf: the caller reads
    the path into bytes itself and reports the path as ``origin``, so the
    error messages the tier produces are the ones the caller expects.

    Args:
        data: The raw bytes of the package.
        base_uri: The URI the document is served from. A hyperlink whose
            relationship target is relative is resolved against it.
        origin: The origin label used in the error messages. ``"<bytes>"`` for
            a byte source, the path for a file source.

    Returns:
        The document, the reader's omission register and the package root's
        relationships.

    Raises:
        WordParseError: If the tier refused the package. The bridge rebuilds
            the Python error from the tier's ``origin`` and ``reason``.
        ValidationError: If the tier answered with something that is not a
            document.
    """
    params: dict[str, Any] = {"bytes_b64": encode_bytes(data)}
    if base_uri is not None:
        params["base_uri"] = base_uri
    if origin is not None:
        params["origin"] = origin
    result = tier_bridge().call("word.read", params)

    payload = result.get("document")
    if not isinstance(payload, dict):
        raise ValidationError(
            "the Kotlin tier returned no Word document",
            field="document",
            value=payload,
        )
    document = WordDocument.from_wire(payload)
    omissions = [str(item) for item in result.get("omissions") or []]
    relationships = tuple(
        WordPartRelationship.from_wire(item)
        for item in result.get("package_relationships") or []
    )
    return WordReadResult(document, omissions, relationships)


def write_document(document: WordDocument, *, deterministic: bool = True) -> bytes:
    """Render a document model into a ``.docx`` package.

    The writer is byte-deterministic by default: the same document always
    produces the same bytes.

    Args:
        document: The document to render.
        deterministic: Whether the output is byte-deterministic.

    Returns:
        The bytes of the package.

    Raises:
        WordRenderError: If the tier refused the document. The bridge rebuilds
            the Python error from the tier's ``reason``.
    """
    return write_document_result(document, deterministic=deterministic).data


def write_document_result(
    document: WordDocument, *, deterministic: bool = True
) -> WordWriteResult:
    """Render a document model and report what the writer could not represent.

    The Kotlin writer keeps an omission register of its own -- a construct it
    could not represent is named there rather than dropped in silence -- and
    WIRE.md §3.2 does not yet carry it across the seam.  This call reads the
    register when the tier sends it and reports an empty one when it does not,
    so the Python side can hold the register without inventing a rule of its
    own.

    Args:
        document: The document to render.
        deterministic: Whether the output is byte-deterministic.

    Returns:
        The bytes of the package and the writer's omission register.

    Raises:
        WordRenderError: If the tier refused the document. The bridge rebuilds
            the Python error from the tier's ``reason``.
    """
    result = tier_bridge().call(
        "word.write",
        {"document": document.to_wire(), "deterministic": deterministic},
    )
    payload = result.get("bytes_b64")
    if not isinstance(payload, str):
        raise ValidationError(
            "the Kotlin tier returned no document bytes",
            field="bytes_b64",
            value=payload,
        )
    omissions = [str(item) for item in result.get("omissions") or []]
    return WordWriteResult(decode_bytes(payload), omissions)


class WordManuscriptResult(NamedTuple):
    """What a manuscript build produced.

    Attributes:
        document: The document the tier built.
        data: The bytes of the package the tier rendered it to.
    """

    document: WordDocument
    data: bytes


def build_manuscript(
    draft: DocumentDraft,
    bibliography: Sequence[BibliographicRecord] = (),
    *,
    verified_only: bool = False,
) -> WordManuscriptResult:
    """Build a manuscript document and render it into a ``.docx`` package.

    The manuscript rules are the Kotlin tier's. The draft and the bibliography
    cross the seam in the core wire form that ``kotlin/core/WireDecode.kt``
    reads, and the answer comes back with the document in the wire form
    ``kotlin/word/WIRE.md`` §2 pins.

    Args:
        draft: The manuscript draft to render.
        bibliography: The bibliographic records for the reference list, in the
            order they should appear.
        verified_only: Whether to drop every section that did not pass
            verification -- heading included.

    Returns:
        The document the tier built and the bytes of its package.

    Raises:
        ValidationError: If the tier answered with something that is not a
            document, or with no package.
    """
    result = tier_bridge().call(
        "word.manuscript",
        {
            "draft": draft.to_wire(),
            "bibliography": [record.to_wire() for record in bibliography],
            "verified_only": verified_only,
        },
    )

    payload = result.get("document")
    if not isinstance(payload, dict):
        raise ValidationError(
            "the Kotlin tier returned no Word document",
            field="document",
            value=payload,
        )
    encoded = result.get("bytes_b64")
    if not isinstance(encoded, str):
        raise ValidationError(
            "the Kotlin tier returned no document bytes",
            field="bytes_b64",
            value=encoded,
        )
    return WordManuscriptResult(WordDocument.from_wire(payload), decode_bytes(encoded))


__all__ = [
    "WordManuscriptResult",
    "WordReadResult",
    "WordWriteResult",
    "read_document",
    "write_document",
    "write_document_result",
    "build_manuscript",
]
