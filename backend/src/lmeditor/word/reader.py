"""Reader for Word (OOXML / ``.docx``) packages -- a door to the Kotlin tier.

This module used to *be* the reader: it parsed the OPC package itself and
mapped it onto the shared, format-neutral model of :mod:`lmeditor.word.model`.
The OOXML rules are the Kotlin tier's now (``kotlin/word/WordReader.kt``), and
this module keeps only the Python API: the same class, the same methods, the
same attributes and the same exceptions, answering with the tier's answers.

What stays here is what is genuinely Python's:

* the **filesystem**.  The tier never touches the filesystem on Python's
  behalf, so the reader reads a path into bytes itself and reports the path as
  the *origin*, which keeps the error messages identical;
* the **error surface**.  A missing path, a path that cannot be read and a
  payload that is not bytes are refused here, exactly as they were; and
* the **model**.  The tier's answer is rebuilt into
  :class:`~lmeditor.word.model.WordDocument` by the model itself, and a
  document the Python model refuses is reported as a
  :class:`~lmeditor.core.exceptions.WordParseError`, as it always was.

There is deliberately **no fallback** to the Python implementation.  A silent
fallback would hide the very drift the tier exists to remove: two
implementations that disagree, with only one of them in the request path.  If
the tier is not there, the reader says so.

The frozen original lives in :mod:`lmeditor._reference.word_reader`.  It is the
yardstick the parity harness measures the tier against, and it is never
rewritten to follow a Kotlin change.
"""

from __future__ import annotations

from pathlib import Path

from lmeditor.core.exceptions import ValidationError, WordParseError
from lmeditor.tier.word import read_document
from lmeditor.word.model import WordDocument, WordPartRelationship

__all__ = ["WordReader"]


class WordReader:
    """Reads ``.docx`` packages into the shared document model.

    The reader is stateless between calls: every ``read``/``read_bytes`` resets
    the omission register and re-reads the package from scratch, so the same
    bytes always yield the same :class:`~lmeditor.word.model.WordDocument`.

    The parsing itself is the Kotlin tier's.  This class is the door onto it:
    it keeps the API, the refusals and the registers, and it answers with the
    tier's document.
    """

    def __init__(self) -> None:
        self.last_omissions: list[str] = []
        self._package_relationships: tuple[WordPartRelationship, ...] = ()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def read(
        self, source: str | Path | bytes, *, base_uri: str | None = None
    ) -> WordDocument:
        """Read a document from a path or from raw package bytes.

        Args:
            source: A filesystem path (``str`` or ``Path``) or the raw bytes of
                a ``.docx`` package.
            base_uri: The URI the document is served from. A hyperlink whose
                relationship target is relative is resolved against it; with no
                base the target is preserved verbatim and recorded in
                :attr:`last_omissions`.

        Returns:
            The parsed document.

        Raises:
            WordParseError: If the package is missing, malformed or cannot be
                turned into the document model.
            BridgeUnavailableError: If the Kotlin tier is not there. The reader
                never falls back to a Python implementation of its own rules.
        """
        self._begin_read()
        if isinstance(source, (bytes, bytearray, memoryview)):
            return self._read_package(bytes(source), "<bytes>", base_uri)
        path = Path(source)
        origin = str(path)
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise WordParseError(origin, f"the file could not be read: {exc}") from exc
        return self._read_package(data, origin, base_uri)

    def read_bytes(self, data: bytes, *, base_uri: str | None = None) -> WordDocument:
        """Read a document from the raw bytes of a ``.docx`` package.

        Args:
            data: The bytes of a ``.docx`` package.
            base_uri: The URI the document is served from. A hyperlink whose
                relationship target is relative is resolved against it; with no
                base the target is preserved verbatim and recorded in
                :attr:`last_omissions`.

        Returns:
            The parsed document.

        Raises:
            WordParseError: If the package is malformed or cannot be turned
                into the document model.
            BridgeUnavailableError: If the Kotlin tier is not there. The reader
                never falls back to a Python implementation of its own rules.
        """
        self._begin_read()
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise WordParseError("<bytes>", "expected the raw bytes of a .docx package")
        return self._read_package(bytes(data), "<bytes>", base_uri)

    @property
    def last_package_relationships(self) -> tuple[WordPartRelationship, ...]:
        """Return the relationships the package root holds, in a stable order.

        The package root reaches parts the document does not, and a part it
        reaches carries that relationship on
        :attr:`~lmeditor.word.model.WordPart.package_relationship`.  A
        package-root relationship that reaches no part of the package -- an
        external one, say -- has no place in the document model, so it is
        kept here rather than being dropped.
        """
        return self._package_relationships

    # ------------------------------------------------------------------
    # Package level
    # ------------------------------------------------------------------

    def _begin_read(self) -> None:
        """Reset the per-read state, including the omission register."""
        self.last_omissions = []
        self._package_relationships = ()

    def _read_package(
        self, data: bytes, origin: str, base_uri: str | None
    ) -> WordDocument:
        """Ask the tier to parse a package and rebuild the document.

        A document the Python model refuses is a malformed package as far as
        the caller is concerned, so it surfaces as a
        :class:`~lmeditor.core.exceptions.WordParseError` -- never as a raw
        ``pydantic.ValidationError``.
        """
        try:
            document, omissions, relationships = read_document(
                data, base_uri=base_uri, origin=origin
            )
        except ValidationError as exc:
            raise WordParseError(
                origin,
                "the document could not be turned into the document model: "
                f"{exc}",
            ) from exc
        self.last_omissions = list(omissions)
        self._package_relationships = tuple(
            sorted(relationships, key=lambda relationship: relationship.relationship_id)
        )
        return document
