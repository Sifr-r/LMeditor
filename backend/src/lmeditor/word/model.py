"""Format-neutral Word document model.

This module is the contract's shared type surface (§3). It knows nothing about
OOXML: the reader maps a ``.docx`` package onto these types, the writer maps
them back, and the manuscript bridge builds them from LMeditor's own domain
models. Because both directions share one model, structure that survives a
round trip is structure the product can rely on.

The module is deliberately dependency-free: ``pydantic`` and the standard
library only. It must never import ``lmeditor.ingestion``, ``lmeditor.workflow``
or ``lmeditor.server``.  It must never import ``lmeditor.tier`` either: the
frozen witnesses in ``lmeditor._reference`` import this module, so a decoder
that reached across the seam would make the witness reach it too.

Every type here also knows its **wire form** -- the plain JSON object the
Kotlin tier and the Python tier exchange (``kotlin/word/WIRE.md`` §2).  Each
type grows a ``from_wire`` decoder and a ``to_wire`` encoder, so the two tiers
describe a document with the same names, the same enum strings and the same
timestamps, and neither can drift from the other without the contract noticing.

The model covers the constructs a research manuscript actually uses: runs with
their formatting, tracked revisions, comments, footnotes, endnotes, sections,
list numbering formats, tables with their cell content, and the media a
document embeds.  Every one of those is represented *explicitly* -- a construct
the model cannot hold is reported through an omission register, never dropped
in silence.
"""

from __future__ import annotations

import base64
from collections.abc import Mapping
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Optional, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

from lmeditor.core.exceptions import ValidationError
from lmeditor.word.text import normalize_whitespace


class WordBlockKind(str, Enum):
    """The structural kinds a Word document is composed of."""

    TITLE = "title"
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST_ITEM = "list_item"
    TABLE = "table"
    QUOTE = "quote"
    CODE = "code"
    PAGE_BREAK = "page_break"


class ListMarker(str, Enum):
    """The marker style of a list item."""

    BULLET = "bullet"
    NUMBER = "number"


class WordRevisionKind(str, Enum):
    """The kinds of tracked revision Word records.

    ``INSERTION`` and ``DELETION`` are the two the reader must never lose;
    ``MOVE_FROM``/``MOVE_TO`` are the move pair, and ``FORMAT_CHANGE`` marks a
    run whose *properties* changed rather than whose text did.
    """

    INSERTION = "insertion"
    DELETION = "deletion"
    MOVE_FROM = "move_from"
    MOVE_TO = "move_to"
    FORMAT_CHANGE = "format_change"


#: Revision kinds whose text is *not* part of the document's final text.
_DELETION_KINDS: frozenset[WordRevisionKind] = frozenset(
    {WordRevisionKind.DELETION, WordRevisionKind.MOVE_FROM}
)


# ---------------------------------------------------------------------------
# The wire form (``kotlin/word/WIRE.md`` §1 and §2)
# ---------------------------------------------------------------------------
#
# The rules the wire form obeys, and that the decoders below enforce:
#
# * a key is ``snake_case`` and named exactly like the model's field;
# * an enum travels as its wire string, never as its name or its ordinal;
# * ``bytes`` travels as a base64 string;
# * a timestamp travels as an ISO-8601 string;
# * an absent key and an explicit ``null`` mean the same thing;
# * an unknown key is ignored, so the wire can grow without breaking a peer;
# * a missing *required* key is an error, never a silent default.
#
# A key is required when the model does not declare the field optional: those
# are exactly the keys the model would refuse the object without, and the
# Kotlin tier's decoder (``kotlin/word/WordWire.kt``) reads them the same
# way.  A field the model declares optional is read as ``None`` when the key
# is absent *or* explicitly null.  The two tiers must agree on which is
# which, so the classification lives in one place on each side and in the
# contract between them.

#: The decoder of one wire value: the value, the key it came from, and the
#: name of the type being decoded.
_WireDecoder = Callable[[Any, str, str], Any]

#: The enum a wire value may carry.
_WireEnum = TypeVar("_WireEnum", bound=Enum)


def _wire_failure(what: str, key: str, expected: str, value: Any) -> ValidationError:
    """Build the error a malformed wire value raises."""
    return ValidationError(
        f"{what}: the wire payload key '{key}' must be {expected}",
        field=key,
        value=value,
    )


def _wire_mapping(payload: Any, what: str) -> Mapping[str, Any]:
    """Return the payload as a mapping, or refuse it."""
    if not isinstance(payload, Mapping):
        raise ValidationError(
            f"{what}: the wire payload must be an object, "
            f"got {type(payload).__name__}",
            field=what,
            value=payload,
        )
    return payload


def _put(
    out: dict[str, Any],
    data: Mapping[str, Any],
    key: str,
    what: str,
    decoder: _WireDecoder,
    *,
    required: bool = False,
) -> None:
    """Decode one key of the wire form into the model's keyword arguments.

    A required key that is absent -- or that is explicitly null -- is an
    error: the model has no default to fall back on, and inventing one would
    let a typo become a silent difference between the two tiers.  Every other
    key is left out of the model when it is absent, so the model's own
    default applies.
    """
    if key not in data or (required and data[key] is None):
        if required:
            raise ValidationError(
                f"{what}: the wire payload does not carry the required key '{key}'",
                field=key,
                value=None,
            )
        return
    out[key] = decoder(data[key], key, what)


def _wire_str(value: Any, key: str, what: str) -> str:
    """Decode a string."""
    if not isinstance(value, str):
        raise _wire_failure(what, key, "a string", value)
    return value


def _wire_opt_str(value: Any, key: str, what: str) -> str | None:
    """Decode a string that may be null."""
    if value is None:
        return None
    return _wire_str(value, key, what)


def _wire_int(value: Any, key: str, what: str) -> int:
    """Decode an integer.

    A boolean is not an integer on the wire, and a float is only an integer
    when it carries no fraction -- the same rule the Kotlin tier applies.
    """
    if isinstance(value, bool):
        raise _wire_failure(what, key, "an integer", value)
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    raise _wire_failure(what, key, "an integer", value)


def _wire_opt_int(value: Any, key: str, what: str) -> int | None:
    """Decode an integer that may be null."""
    if value is None:
        return None
    return _wire_int(value, key, what)


def _wire_bool(value: Any, key: str, what: str) -> bool:
    """Decode a boolean."""
    if not isinstance(value, bool):
        raise _wire_failure(what, key, "a boolean", value)
    return value


def _wire_str_tuple(value: Any, key: str, what: str) -> tuple[str, ...]:
    """Decode an array of strings."""
    if not isinstance(value, (list, tuple)):
        raise _wire_failure(what, key, "an array of strings", value)
    return tuple(_wire_str(item, key, what) for item in value)


def _wire_int_tuple(value: Any, key: str, what: str) -> tuple[int, ...]:
    """Decode an array of integers."""
    if not isinstance(value, (list, tuple)):
        raise _wire_failure(what, key, "an array of integers", value)
    return tuple(_wire_int(item, key, what) for item in value)


def _wire_extent(value: Any, key: str, what: str) -> tuple[int, int] | None:
    """Decode the (width, height) pair an anchored body element is drawn at."""
    if value is None:
        return None
    if not isinstance(value, (list, tuple)):
        raise _wire_failure(what, key, "an array of two integers", value)
    if len(value) != 2:
        raise ValidationError(
            f"{what}: the wire payload key '{key}' must hold exactly two "
            f"integers, got {len(value)}",
            field=key,
            value=list(value),
        )
    return (_wire_int(value[0], key, what), _wire_int(value[1], key, what))


def _wire_bytes(value: Any, key: str, what: str) -> bytes:
    """Decode a base64 payload."""
    if not isinstance(value, str):
        raise _wire_failure(what, key, "a base64 string", value)
    try:
        return base64.b64decode(value.encode("ascii"), validate=True)
    except (ValueError, UnicodeEncodeError) as exc:
        raise ValidationError(
            f"{what}: {key!r} is not valid base64: {exc}",
            field=key,
            value=value,
        ) from exc


def _wire_datetime(value: Any, key: str, what: str) -> datetime | None:
    """Decode an ISO-8601 timestamp.

    A timestamp with an offset keeps it; a timestamp without one stays naive.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise _wire_failure(what, key, "an ISO-8601 timestamp", value)
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValidationError(
            f"{what}: {key!r} is not an ISO-8601 timestamp: {exc}",
            field=key,
            value=value,
        ) from exc


def _wire_enum(value: Any, key: str, what: str, enum_class: type[_WireEnum]) -> _WireEnum:
    """Decode an enum from its wire string -- never from its name."""
    if not isinstance(value, str):
        raise _wire_failure(what, key, f"a {enum_class.__name__} wire string", value)
    try:
        return enum_class(value)
    except ValueError as exc:
        raise ValidationError(
            f"{what}: {key!r} is not a {enum_class.__name__} wire value: {value!r}",
            field=key,
            value=value,
        ) from exc


def _wire_block_kind(value: Any, key: str, what: str) -> WordBlockKind:
    """Decode a :class:`WordBlockKind`."""
    return _wire_enum(value, key, what, WordBlockKind)


def _wire_list_marker(value: Any, key: str, what: str) -> ListMarker | None:
    """Decode a :class:`ListMarker`, which the model declares optional."""
    if value is None:
        return None
    return _wire_enum(value, key, what, ListMarker)


def _wire_revision_kind(value: Any, key: str, what: str) -> WordRevisionKind:
    """Decode a :class:`WordRevisionKind`."""
    return _wire_enum(value, key, what, WordRevisionKind)


def _wire_timestamp(value: datetime | None) -> str | None:
    """Encode a timestamp the way ``datetime.isoformat`` writes it."""
    return value.isoformat() if value is not None else None


def _wire_payload(data: bytes) -> str:
    """Encode a binary payload as base64."""
    return base64.b64encode(data).decode("ascii")


# The nested decoders below name the model classes, which are defined further
# down this module.  That is deliberate and safe: a function body resolves the
# global name when it is *called*, and by then the module is fully loaded.


def _wire_revision(value: Any, key: str, what: str) -> WordRevision | None:
    """Decode a tracked revision."""
    if value is None:
        return None
    return WordRevision.from_wire(_wire_mapping(value, what))


def _wire_run(value: Any, key: str, what: str) -> WordRun:
    """Decode a run."""
    return WordRun.from_wire(_wire_mapping(value, what))


def _wire_run_tuple(value: Any, key: str, what: str) -> tuple[WordRun, ...]:
    """Decode an array of runs."""
    if not isinstance(value, (list, tuple)):
        raise _wire_failure(what, key, "an array of runs", value)
    return tuple(_wire_run(item, key, what) for item in value)


def _wire_comment(value: Any, key: str, what: str) -> WordComment:
    """Decode a comment."""
    return WordComment.from_wire(_wire_mapping(value, what))


def _wire_comment_tuple(value: Any, key: str, what: str) -> tuple[WordComment, ...]:
    """Decode an array of comments."""
    if not isinstance(value, (list, tuple)):
        raise _wire_failure(what, key, "an array of comments", value)
    return tuple(_wire_comment(item, key, what) for item in value)


def _wire_footnote(value: Any, key: str, what: str) -> WordFootnote:
    """Decode a footnote."""
    return WordFootnote.from_wire(_wire_mapping(value, what))


def _wire_footnote_tuple(value: Any, key: str, what: str) -> tuple[WordFootnote, ...]:
    """Decode an array of footnotes."""
    if not isinstance(value, (list, tuple)):
        raise _wire_failure(what, key, "an array of footnotes", value)
    return tuple(_wire_footnote(item, key, what) for item in value)


def _wire_endnote(value: Any, key: str, what: str) -> WordEndnote:
    """Decode an endnote."""
    return WordEndnote.from_wire(_wire_mapping(value, what))


def _wire_endnote_tuple(value: Any, key: str, what: str) -> tuple[WordEndnote, ...]:
    """Decode an array of endnotes."""
    if not isinstance(value, (list, tuple)):
        raise _wire_failure(what, key, "an array of endnotes", value)
    return tuple(_wire_endnote(item, key, what) for item in value)


def _wire_header_footer_ref(value: Any, key: str, what: str) -> WordHeaderFooterRef:
    """Decode a header or footer reference."""
    return WordHeaderFooterRef.from_wire(_wire_mapping(value, what))


def _wire_header_footer_ref_tuple(
    value: Any, key: str, what: str
) -> tuple[WordHeaderFooterRef, ...]:
    """Decode an array of header or footer references."""
    if not isinstance(value, (list, tuple)):
        raise _wire_failure(what, key, "an array of header or footer references", value)
    return tuple(_wire_header_footer_ref(item, key, what) for item in value)


def _wire_section(value: Any, key: str, what: str) -> WordSection:
    """Decode a section."""
    return WordSection.from_wire(_wire_mapping(value, what))


def _wire_section_tuple(value: Any, key: str, what: str) -> tuple[WordSection, ...]:
    """Decode an array of sections."""
    if not isinstance(value, (list, tuple)):
        raise _wire_failure(what, key, "an array of sections", value)
    return tuple(_wire_section(item, key, what) for item in value)


def _wire_style(value: Any, key: str, what: str) -> WordStyle:
    """Decode a style definition."""
    return WordStyle.from_wire(_wire_mapping(value, what))


def _wire_style_tuple(value: Any, key: str, what: str) -> tuple[WordStyle, ...]:
    """Decode an array of style definitions."""
    if not isinstance(value, (list, tuple)):
        raise _wire_failure(what, key, "an array of styles", value)
    return tuple(_wire_style(item, key, what) for item in value)


def _wire_media(value: Any, key: str, what: str) -> WordMedia:
    """Decode an embedded media part."""
    return WordMedia.from_wire(_wire_mapping(value, what))


def _wire_media_tuple(value: Any, key: str, what: str) -> tuple[WordMedia, ...]:
    """Decode an array of embedded media parts."""
    if not isinstance(value, (list, tuple)):
        raise _wire_failure(what, key, "an array of media parts", value)
    return tuple(_wire_media(item, key, what) for item in value)


def _wire_part_relationship(value: Any, key: str, what: str) -> WordPartRelationship:
    """Decode a relationship."""
    return WordPartRelationship.from_wire(_wire_mapping(value, what))


def _wire_part_relationship_tuple(
    value: Any, key: str, what: str
) -> tuple[WordPartRelationship, ...]:
    """Decode an array of relationships."""
    if not isinstance(value, (list, tuple)):
        raise _wire_failure(what, key, "an array of relationships", value)
    return tuple(_wire_part_relationship(item, key, what) for item in value)


def _wire_opt_part_relationship(value: Any, key: str, what: str) -> WordPartRelationship | None:
    """Decode a relationship that may be null."""
    if value is None:
        return None
    return _wire_part_relationship(value, key, what)


def _wire_part(value: Any, key: str, what: str) -> WordPart:
    """Decode a carried package part."""
    return WordPart.from_wire(_wire_mapping(value, what))


def _wire_part_tuple(value: Any, key: str, what: str) -> tuple[WordPart, ...]:
    """Decode an array of carried package parts."""
    if not isinstance(value, (list, tuple)):
        raise _wire_failure(what, key, "an array of package parts", value)
    return tuple(_wire_part(item, key, what) for item in value)


def _wire_table(value: Any, key: str, what: str) -> WordTable:
    """Decode a table."""
    return WordTable.from_wire(_wire_mapping(value, what))


def _wire_opt_table(value: Any, key: str, what: str) -> WordTable | None:
    """Decode a table that may be null."""
    if value is None:
        return None
    return _wire_table(value, key, what)


def _wire_table_row(value: Any, key: str, what: str) -> WordTableRow:
    """Decode a table row."""
    return WordTableRow.from_wire(_wire_mapping(value, what))


def _wire_table_cell(value: Any, key: str, what: str) -> WordTableCell:
    """Decode a table cell."""
    return WordTableCell.from_wire(_wire_mapping(value, what))


def _wire_table_cell_tuple(value: Any, key: str, what: str) -> tuple[WordTableCell, ...]:
    """Decode an array of table cells."""
    if not isinstance(value, (list, tuple)):
        raise _wire_failure(what, key, "an array of table cells", value)
    return tuple(_wire_table_cell(item, key, what) for item in value)


def _wire_table_row_tuple(value: Any, key: str, what: str) -> tuple[WordTableRow, ...]:
    """Decode an array of table rows."""
    if not isinstance(value, (list, tuple)):
        raise _wire_failure(what, key, "an array of table rows", value)
    return tuple(_wire_table_row(item, key, what) for item in value)


def _wire_table_tuple(value: Any, key: str, what: str) -> tuple[WordTable, ...]:
    """Decode an array of tables."""
    if not isinstance(value, (list, tuple)):
        raise _wire_failure(what, key, "an array of tables", value)
    return tuple(_wire_table(item, key, what) for item in value)


def _wire_block(value: Any, key: str, what: str) -> WordBlock:
    """Decode a block."""
    return WordBlock.from_wire(_wire_mapping(value, what))


def _wire_block_tuple(value: Any, key: str, what: str) -> tuple[WordBlock, ...]:
    """Decode an array of blocks."""
    if not isinstance(value, (list, tuple)):
        raise _wire_failure(what, key, "an array of blocks", value)
    return tuple(_wire_block(item, key, what) for item in value)


def _wire_properties(value: Any, key: str, what: str) -> WordProperties:
    """Decode the core properties of a document."""
    return WordProperties.from_wire(_wire_mapping(value, what))


class WordRevision(BaseModel):
    """One tracked revision, as Word records it.

    A revision is identified by ``revision_id``: every run that belongs to the
    same ``w:ins``/``w:del`` element carries the same id, which is how the
    writer can put the runs back into a single revision element.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: WordRevisionKind = Field(description="What kind of revision this is")
    author: str = Field(default="", description="Author of the revision")
    date: Optional[datetime] = Field(default=None, description="Timestamp of the revision")
    revision_id: str = Field(
        default="",
        description="Identifier shared by the runs of one revision element",
    )

    @classmethod
    def from_wire(cls, payload: Mapping[str, Any]) -> "WordRevision":
        """Build a revision from its wire form (WIRE.md §2.4).

        Raises:
            ValidationError: If a required key is missing or a value is
                malformed.  A missing key is never answered with a default.
        """
        data = _wire_mapping(payload, "WordRevision")
        out: dict[str, Any] = {}
        _put(out, data, "kind", "WordRevision", _wire_revision_kind, required=True)
        _put(out, data, "author", "WordRevision", _wire_str, required=True)
        _put(out, data, "date", "WordRevision", _wire_datetime)
        _put(out, data, "revision_id", "WordRevision", _wire_str, required=True)
        return cls.model_validate(out)

    def to_wire(self) -> dict[str, Any]:
        """The revision's wire form (WIRE.md §2.4)."""
        return {
            "kind": self.kind.value,
            "author": self.author,
            "date": _wire_timestamp(self.date),
            "revision_id": self.revision_id,
        }


class WordRun(BaseModel):
    """A run of inline text carrying its own formatting."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str = Field(default="", description="Verbatim run text, not pre-stripped")
    bold: bool = Field(default=False, description="Render the run in bold")
    italic: bool = Field(default=False, description="Render the run in italic")
    underline: bool = Field(default=False, description="Render the run underlined")
    superscript: bool = Field(default=False, description="Render the run as superscript")
    subscript: bool = Field(default=False, description="Render the run as subscript")
    strike: bool = Field(default=False, description="Render the run struck through")
    font: Optional[str] = Field(default=None, description="Font family of the run")
    size: Optional[int] = Field(
        default=None,
        ge=1,
        description="Font size in half-points, the unit Word uses",
    )
    color: Optional[str] = Field(
        default=None,
        description="Font colour as a six-digit RRGGBB hex string",
    )
    highlight: Optional[str] = Field(
        default=None,
        description="Highlight colour, spelled the way Word spells it",
    )
    hyperlink: Optional[str] = Field(
        default=None,
        description="Absolute hyperlink target for the run",
    )
    footnote_ref: Optional[str] = Field(
        default=None,
        description="Id of the WordFootnote this run references",
    )
    endnote_ref: Optional[str] = Field(
        default=None,
        description="Id of the WordEndnote this run references",
    )
    revision: Optional[WordRevision] = Field(
        default=None,
        description="Tracked revision this run belongs to, when it has one",
    )
    comment_refs: tuple[str, ...] = Field(
        default=(),
        description="Ids of the comments whose range covers this run",
    )
    image_ref: Optional[str] = Field(
        default=None,
        description="Id of the WordMedia this run embeds",
    )
    equation_xml: Optional[str] = Field(
        default=None,
        description="Raw OMML markup of an equation, preserved verbatim for re-emission",
    )
    part_ref: Optional[str] = Field(
        default=None,
        description="Package part a non-image body element anchors",
    )
    anchor_extent: Optional[tuple[int, int]] = Field(
        default=None,
        description=(
            "Size the anchored body element is drawn at, in EMUs, as "
            "(width, height).  Word scales a chart or a picture by rewriting "
            "the extent of the drawing, so without it the element comes back "
            "at a default size instead of the size it was written at."
        ),
    )
    format_change_xml: Optional[str] = Field(
        default=None,
        description=(
            "Raw XML of the tracked formatting revision (w:rPrChange) that "
            "preceded these properties, preserved verbatim for re-emission"
        ),
    )

    @classmethod
    def from_wire(cls, payload: Mapping[str, Any]) -> "WordRun":
        """Build a run from its wire form (WIRE.md §2.3).

        Raises:
            ValidationError: If a value is malformed.
        """
        data = _wire_mapping(payload, "WordRun")
        out: dict[str, Any] = {}
        _put(out, data, "text", "WordRun", _wire_str, required=True)
        _put(out, data, "bold", "WordRun", _wire_bool, required=True)
        _put(out, data, "italic", "WordRun", _wire_bool, required=True)
        _put(out, data, "underline", "WordRun", _wire_bool, required=True)
        _put(out, data, "superscript", "WordRun", _wire_bool, required=True)
        _put(out, data, "subscript", "WordRun", _wire_bool, required=True)
        _put(out, data, "strike", "WordRun", _wire_bool, required=True)
        _put(out, data, "font", "WordRun", _wire_opt_str)
        _put(out, data, "size", "WordRun", _wire_opt_int)
        _put(out, data, "color", "WordRun", _wire_opt_str)
        _put(out, data, "highlight", "WordRun", _wire_opt_str)
        _put(out, data, "hyperlink", "WordRun", _wire_opt_str)
        _put(out, data, "footnote_ref", "WordRun", _wire_opt_str)
        _put(out, data, "endnote_ref", "WordRun", _wire_opt_str)
        _put(out, data, "revision", "WordRun", _wire_revision)
        _put(out, data, "comment_refs", "WordRun", _wire_str_tuple, required=True)
        _put(out, data, "image_ref", "WordRun", _wire_opt_str)
        _put(out, data, "equation_xml", "WordRun", _wire_opt_str)
        _put(out, data, "part_ref", "WordRun", _wire_opt_str)
        _put(out, data, "anchor_extent", "WordRun", _wire_extent)
        _put(out, data, "format_change_xml", "WordRun", _wire_opt_str)
        return cls.model_validate(out)

    def to_wire(self) -> dict[str, Any]:
        """The run's wire form (WIRE.md §2.3)."""
        return {
            "text": self.text,
            "bold": self.bold,
            "italic": self.italic,
            "underline": self.underline,
            "superscript": self.superscript,
            "subscript": self.subscript,
            "strike": self.strike,
            "font": self.font,
            "size": self.size,
            "color": self.color,
            "highlight": self.highlight,
            "hyperlink": self.hyperlink,
            "footnote_ref": self.footnote_ref,
            "endnote_ref": self.endnote_ref,
            "revision": self.revision.to_wire() if self.revision is not None else None,
            "comment_refs": list(self.comment_refs),
            "image_ref": self.image_ref,
            "equation_xml": self.equation_xml,
            "part_ref": self.part_ref,
            "anchor_extent": (
                list(self.anchor_extent) if self.anchor_extent is not None else None
            ),
            "format_change_xml": self.format_change_xml,
        }

    @property
    def is_deleted(self) -> bool:
        """Report whether the run's text was deleted by a tracked revision.

        A deleted run is part of the model -- its text is recoverable and it
        survives a round trip -- but it is not part of the document's *final*
        text, so it never reaches the plain-text projection.
        """
        return self.revision is not None and self.revision.kind in _DELETION_KINDS


class WordComment(BaseModel):
    """A comment anchored on a range of the document.

    A comment is anchored by the runs it covers: every run inside the range
    carries the comment's id in :attr:`WordRun.comment_refs`.  The block the
    comment lives in and the text of the range are kept as well, so an
    operator can see what was being talked about even when the anchor itself
    cannot be rebuilt.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    comment_id: str = Field(min_length=1, description="Identifier of the comment")
    author: str = Field(default="", description="Author of the comment")
    initials: str = Field(default="", description="Initials of the author")
    date: Optional[datetime] = Field(default=None, description="Timestamp of the comment")
    text: str = Field(default="", description="Normalised text of the comment")
    runs: tuple[WordRun, ...] = Field(default=(), description="Inline runs, in order")
    anchor_block_id: Optional[str] = Field(
        default=None,
        description="Id of the block the comment is anchored in",
    )
    anchor_text: str = Field(
        default="",
        description="Text of the range the comment is anchored on",
    )

    @classmethod
    def from_wire(cls, payload: Mapping[str, Any]) -> "WordComment":
        """Build a comment from its wire form (WIRE.md §2.9).

        Raises:
            ValidationError: If a required key is missing or a value is
                malformed.
        """
        data = _wire_mapping(payload, "WordComment")
        out: dict[str, Any] = {}
        _put(out, data, "comment_id", "WordComment", _wire_str, required=True)
        _put(out, data, "author", "WordComment", _wire_str, required=True)
        _put(out, data, "initials", "WordComment", _wire_str, required=True)
        _put(out, data, "date", "WordComment", _wire_datetime)
        _put(out, data, "text", "WordComment", _wire_str, required=True)
        _put(out, data, "runs", "WordComment", _wire_run_tuple, required=True)
        _put(out, data, "anchor_block_id", "WordComment", _wire_opt_str)
        _put(out, data, "anchor_text", "WordComment", _wire_str, required=True)
        return cls.model_validate(out)

    def to_wire(self) -> dict[str, Any]:
        """The comment's wire form (WIRE.md §2.9)."""
        return {
            "comment_id": self.comment_id,
            "author": self.author,
            "initials": self.initials,
            "date": _wire_timestamp(self.date),
            "text": self.text,
            "runs": [run.to_wire() for run in self.runs],
            "anchor_block_id": self.anchor_block_id,
            "anchor_text": self.anchor_text,
        }


class WordFootnote(BaseModel):
    """A footnote referenced by one or more runs."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    footnote_id: str = Field(min_length=1, description="Identifier referenced by runs")
    text: str = Field(default="", description="Normalised text of the footnote")
    runs: tuple[WordRun, ...] = Field(default=(), description="Inline runs, in order")

    @classmethod
    def from_wire(cls, payload: Mapping[str, Any]) -> "WordFootnote":
        """Build a footnote from its wire form (WIRE.md §2.8).

        Raises:
            ValidationError: If a required key is missing or a value is
                malformed.
        """
        data = _wire_mapping(payload, "WordFootnote")
        out: dict[str, Any] = {}
        _put(out, data, "footnote_id", "WordFootnote", _wire_str, required=True)
        _put(out, data, "text", "WordFootnote", _wire_str, required=True)
        _put(out, data, "runs", "WordFootnote", _wire_run_tuple, required=True)
        return cls.model_validate(out)

    def to_wire(self) -> dict[str, Any]:
        """The footnote's wire form (WIRE.md §2.8)."""
        return {
            "footnote_id": self.footnote_id,
            "text": self.text,
            "runs": [run.to_wire() for run in self.runs],
        }


class WordEndnote(BaseModel):
    """An endnote referenced by one or more runs.

    Endnotes are the closing-note counterpart of footnotes: Word collects them
    at the end of the document rather than at the foot of the page.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    endnote_id: str = Field(min_length=1, description="Identifier referenced by runs")
    text: str = Field(default="", description="Normalised text of the endnote")
    runs: tuple[WordRun, ...] = Field(default=(), description="Inline runs, in order")

    @classmethod
    def from_wire(cls, payload: Mapping[str, Any]) -> "WordEndnote":
        """Build an endnote from its wire form (WIRE.md §2.8).

        Raises:
            ValidationError: If a required key is missing or a value is
                malformed.
        """
        data = _wire_mapping(payload, "WordEndnote")
        out: dict[str, Any] = {}
        _put(out, data, "endnote_id", "WordEndnote", _wire_str, required=True)
        _put(out, data, "text", "WordEndnote", _wire_str, required=True)
        _put(out, data, "runs", "WordEndnote", _wire_run_tuple, required=True)
        return cls.model_validate(out)

    def to_wire(self) -> dict[str, Any]:
        """The endnote's wire form (WIRE.md §2.8)."""
        return {
            "endnote_id": self.endnote_id,
            "text": self.text,
            "runs": [run.to_wire() for run in self.runs],
        }


class WordHeaderFooterRef(BaseModel):
    """One header or footer reference of a section.

    The reference *type* is what makes a header findable: a document whose
    ``header1.xml`` holds the even-page header must not have its headers
    matched by part order, because that swaps them.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    ref_type: str = Field(
        default="default",
        description="Reference type: 'default', 'even' or 'first'",
    )
    part_name: str = Field(default="", description="Package part the reference points at")
    text: str = Field(default="", description="Text of the header or footer")

    @classmethod
    def from_wire(cls, payload: Mapping[str, Any]) -> "WordHeaderFooterRef":
        """Build a header or footer reference from its wire form (WIRE.md §2.10).

        Raises:
            ValidationError: If a value is malformed.
        """
        data = _wire_mapping(payload, "WordHeaderFooterRef")
        out: dict[str, Any] = {}
        _put(out, data, "ref_type", "WordHeaderFooterRef", _wire_str, required=True)
        _put(out, data, "part_name", "WordHeaderFooterRef", _wire_str, required=True)
        _put(out, data, "text", "WordHeaderFooterRef", _wire_str, required=True)
        return cls.model_validate(out)

    def to_wire(self) -> dict[str, Any]:
        """The reference's wire form (WIRE.md §2.10)."""
        return {
            "ref_type": self.ref_type,
            "part_name": self.part_name,
            "text": self.text,
        }


class WordSection(BaseModel):
    """The properties of one section of the document.

    The values are the ones Word itself uses: page dimensions and margins are
    twips (1/1440 inch), and the header/footer references are carried by
    type so that they survive a round trip.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    page_width: int = Field(default=12240, ge=1, description="Page width in twips")
    page_height: int = Field(default=15840, ge=1, description="Page height in twips")
    orientation: str = Field(
        default="portrait",
        description="Page orientation: 'portrait' or 'landscape'",
    )
    margin_top: int = Field(default=1440, ge=0, description="Top margin in twips")
    margin_right: int = Field(default=1440, ge=0, description="Right margin in twips")
    margin_bottom: int = Field(default=1440, ge=0, description="Bottom margin in twips")
    margin_left: int = Field(default=1440, ge=0, description="Left margin in twips")
    header_refs: tuple[WordHeaderFooterRef, ...] = Field(
        default=(),
        description="Header references of the section, by type",
    )
    footer_refs: tuple[WordHeaderFooterRef, ...] = Field(
        default=(),
        description="Footer references of the section, by type",
    )
    end_block_id: Optional[str] = Field(
        default=None,
        description="Id of the last block the section covers",
    )

    @classmethod
    def from_wire(cls, payload: Mapping[str, Any]) -> "WordSection":
        """Build a section from its wire form (WIRE.md §2.11).

        Raises:
            ValidationError: If a value is malformed.
        """
        data = _wire_mapping(payload, "WordSection")
        out: dict[str, Any] = {}
        _put(out, data, "page_width", "WordSection", _wire_int, required=True)
        _put(out, data, "page_height", "WordSection", _wire_int, required=True)
        _put(out, data, "orientation", "WordSection", _wire_str, required=True)
        _put(out, data, "margin_top", "WordSection", _wire_int, required=True)
        _put(out, data, "margin_right", "WordSection", _wire_int, required=True)
        _put(out, data, "margin_bottom", "WordSection", _wire_int, required=True)
        _put(out, data, "margin_left", "WordSection", _wire_int, required=True)
        _put(
            out,
            data,
            "header_refs",
            "WordSection",
            _wire_header_footer_ref_tuple,
            required=True,
        )
        _put(
            out,
            data,
            "footer_refs",
            "WordSection",
            _wire_header_footer_ref_tuple,
            required=True,
        )
        _put(out, data, "end_block_id", "WordSection", _wire_opt_str)
        return cls.model_validate(out)

    def to_wire(self) -> dict[str, Any]:
        """The section's wire form (WIRE.md §2.11)."""
        return {
            "page_width": self.page_width,
            "page_height": self.page_height,
            "orientation": self.orientation,
            "margin_top": self.margin_top,
            "margin_right": self.margin_right,
            "margin_bottom": self.margin_bottom,
            "margin_left": self.margin_left,
            "header_refs": [ref.to_wire() for ref in self.header_refs],
            "footer_refs": [ref.to_wire() for ref in self.footer_refs],
            "end_block_id": self.end_block_id,
        }


class WordStyle(BaseModel):
    """A paragraph style definition read from ``word/styles.xml``.

    A style the writer does not know is *defined* here rather than dropped:
    the writer emits the definition into the output package, so a custom
    style survives the round trip.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    style_id: str = Field(min_length=1, description="Style id, as Word spells it")
    name: str = Field(default="", description="Style name")
    based_on: str = Field(default="", description="Id of the style this one is based on")
    style_type: str = Field(default="paragraph", description="Kind of style")
    num_id: Optional[str] = Field(default=None, description="Numbering id the style carries")
    num_level: int = Field(default=0, ge=0, description="Numbering level the style carries")

    @classmethod
    def from_wire(cls, payload: Mapping[str, Any]) -> "WordStyle":
        """Build a style definition from its wire form (WIRE.md §2.12).

        Raises:
            ValidationError: If a required key is missing or a value is
                malformed.
        """
        data = _wire_mapping(payload, "WordStyle")
        out: dict[str, Any] = {}
        _put(out, data, "style_id", "WordStyle", _wire_str, required=True)
        _put(out, data, "name", "WordStyle", _wire_str, required=True)
        _put(out, data, "based_on", "WordStyle", _wire_str, required=True)
        _put(out, data, "style_type", "WordStyle", _wire_str, required=True)
        _put(out, data, "num_id", "WordStyle", _wire_opt_str)
        _put(out, data, "num_level", "WordStyle", _wire_int, required=True)
        return cls.model_validate(out)

    def to_wire(self) -> dict[str, Any]:
        """The style's wire form (WIRE.md §2.12)."""
        return {
            "style_id": self.style_id,
            "name": self.name,
            "based_on": self.based_on,
            "style_type": self.style_type,
            "num_id": self.num_id,
            "num_level": self.num_level,
        }


class WordMedia(BaseModel):
    """An embedded media part (an image) of the package.

    The bytes are kept, so an image is not lost by being read: the writer can
    put the part back into the package and re-anchor it in the text.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    media_id: str = Field(min_length=1, description="Identifier referenced by runs")
    part_name: str = Field(min_length=1, description="Package part name of the media")
    content_type: str = Field(default="", description="Content type of the media part")
    data: bytes = Field(default=b"", description="Raw bytes of the media part")
    relationship_id: str = Field(default="", description="Relationship the part was reached by")

    @classmethod
    def from_wire(cls, payload: Mapping[str, Any]) -> "WordMedia":
        """Build a media part from its wire form (WIRE.md §2.13).

        Raises:
            ValidationError: If a required key is missing or a value is
                malformed.
        """
        data = _wire_mapping(payload, "WordMedia")
        out: dict[str, Any] = {}
        _put(out, data, "media_id", "WordMedia", _wire_str, required=True)
        _put(out, data, "part_name", "WordMedia", _wire_str, required=True)
        _put(out, data, "content_type", "WordMedia", _wire_str, required=True)
        _put(out, data, "data", "WordMedia", _wire_bytes, required=True)
        _put(out, data, "relationship_id", "WordMedia", _wire_str, required=True)
        return cls.model_validate(out)

    def to_wire(self) -> dict[str, Any]:
        """The media part's wire form (WIRE.md §2.13)."""
        return {
            "media_id": self.media_id,
            "part_name": self.part_name,
            "content_type": self.content_type,
            "data": _wire_payload(self.data),
            "relationship_id": self.relationship_id,
        }


class WordPartRelationship(BaseModel):
    """A relationship a carried part holds to another part of the package.

    A carried part is not always reached from the document: a chart's colour
    style, its quick style and the workbook embedded behind it are reached
    from the chart's *own* relationship part, not from ``document.xml``.  Those
    relationships travel with the part, so the writer can put the package back
    the way it found it.

    The relationship part itself is never carried: a ``.rels`` file is the
    relationship list of a sibling part, not content.  Handing it to Word as
    content is what makes Word report the package as corrupt.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    relationship_id: str = Field(
        min_length=1,
        description="Id the relationship is known by inside the owning part",
    )
    relationship_type: str = Field(
        min_length=1,
        description="Type of the relationship",
    )
    target: str = Field(
        min_length=1,
        description="Package part name of the target, or its URL when it is external",
    )
    is_external: bool = Field(
        default=False,
        description="Target lies outside the package",
    )

    @classmethod
    def from_wire(cls, payload: Mapping[str, Any]) -> "WordPartRelationship":
        """Build a relationship from its wire form (WIRE.md §2.15).

        Raises:
            ValidationError: If a required key is missing or a value is
                malformed.
        """
        data = _wire_mapping(payload, "WordPartRelationship")
        out: dict[str, Any] = {}
        _put(out, data, "relationship_id", "WordPartRelationship", _wire_str, required=True)
        _put(
            out,
            data,
            "relationship_type",
            "WordPartRelationship",
            _wire_str,
            required=True,
        )
        _put(out, data, "target", "WordPartRelationship", _wire_str, required=True)
        _put(out, data, "is_external", "WordPartRelationship", _wire_bool, required=True)
        return cls.model_validate(out)

    def to_wire(self) -> dict[str, Any]:
        """The relationship's wire form (WIRE.md §2.15)."""
        return {
            "relationship_id": self.relationship_id,
            "relationship_type": self.relationship_type,
            "target": self.target,
            "is_external": self.is_external,
        }


class WordPart(BaseModel):
    """A package part the reader carried through without modelling its content.

    The bytes are kept so the part is not lost by being read: the writer can
    put it back into the package under the same name.  A chart, an embedded
    object, a glossary document and every other part the model does not
    represent travel this way, so reading a document never costs it a part.

    ``relationship_id`` and ``relationship_type`` say how the part was reached
    from the body: the writer re-creates the relationship under the same id and
    points the body reference that anchored the part at it again.  A part the
    document did *not* reach has neither: it is reached from the relationship
    of the part that owns it, which is what ``relationships`` records, or from
    the package root, which is what ``package_relationship`` records.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    part_name: str = Field(
        min_length=1,
        description="Package part name, e.g. 'word/charts/chart1.xml'",
    )
    content_type: str = Field(default="", description="Content type of the part")
    data: bytes = Field(default=b"", description="Raw bytes of the part")
    relationship_id: str = Field(default="", description="Relationship the part was reached by")
    relationship_type: str = Field(
        default="",
        description="Type of the relationship the part was reached by",
    )
    relationships: tuple[WordPartRelationship, ...] = Field(
        default=(),
        description="Relationships the part holds to other parts of the package",
    )
    package_relationship: Optional[WordPartRelationship] = Field(
        default=None,
        description=(
            "The relationship of the package root that reaches this part, when "
            "the package root reaches it.  A custom document properties part "
            "is reached from the package root and not from the document, so "
            "without this the part would have no path into the package at all."
        ),
    )

    @classmethod
    def from_wire(cls, payload: Mapping[str, Any]) -> "WordPart":
        """Build a carried package part from its wire form (WIRE.md §2.14).

        Raises:
            ValidationError: If a required key is missing or a value is
                malformed.
        """
        data = _wire_mapping(payload, "WordPart")
        out: dict[str, Any] = {}
        _put(out, data, "part_name", "WordPart", _wire_str, required=True)
        _put(out, data, "content_type", "WordPart", _wire_str, required=True)
        _put(out, data, "data", "WordPart", _wire_bytes, required=True)
        _put(out, data, "relationship_id", "WordPart", _wire_str, required=True)
        _put(out, data, "relationship_type", "WordPart", _wire_str, required=True)
        _put(
            out,
            data,
            "relationships",
            "WordPart",
            _wire_part_relationship_tuple,
            required=True,
        )
        _put(out, data, "package_relationship", "WordPart", _wire_opt_part_relationship)
        return cls.model_validate(out)

    def to_wire(self) -> dict[str, Any]:
        """The part's wire form (WIRE.md §2.14)."""
        return {
            "part_name": self.part_name,
            "content_type": self.content_type,
            "data": _wire_payload(self.data),
            "relationship_id": self.relationship_id,
            "relationship_type": self.relationship_type,
            "relationships": [rel.to_wire() for rel in self.relationships],
            "package_relationship": (
                self.package_relationship.to_wire()
                if self.package_relationship is not None
                else None
            ),
        }


class WordTableCell(BaseModel):
    """One cell of a Word table.

    A cell occupies ``row_span`` rows and ``col_span`` columns of the table
    grid, counting its own row and column. A vertically merged cell is
    represented the way Word itself represents it: the cell that *starts* the
    merge carries the span and the text, and the cells below it are flagged
    ``is_merge_continuation`` with no text of their own.

    A cell also carries its *content*: the inline runs of its paragraphs and
    the tables nested inside it.  The flat ``text`` remains the normalised
    projection of that content, so a cell that holds nothing but a nested
    table still renders as text.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str = Field(default="", description="Normalised text of the cell")
    row_span: int = Field(
        default=1,
        ge=1,
        description="Rows occupied by the cell, counting its own row",
    )
    col_span: int = Field(
        default=1,
        ge=1,
        description="Columns occupied by the cell, counting its own column",
    )
    is_header: bool = Field(default=False, description="Cell belongs to a header row")
    is_merge_continuation: bool = Field(
        default=False,
        description="Cell continues a vertical merge started in the row above",
    )
    width: Optional[int] = Field(
        default=None,
        ge=1,
        description="Width of the cell in twips",
    )
    runs: tuple[WordRun, ...] = Field(default=(), description="Inline runs of the cell")
    tables: tuple["WordTable", ...] = Field(
        default=(),
        description="Tables nested inside the cell",
    )

    @classmethod
    def from_wire(cls, payload: Mapping[str, Any]) -> "WordTableCell":
        """Build a table cell from its wire form (WIRE.md §2.7).

        Raises:
            ValidationError: If a value is malformed.
        """
        data = _wire_mapping(payload, "WordTableCell")
        out: dict[str, Any] = {}
        _put(out, data, "text", "WordTableCell", _wire_str, required=True)
        _put(out, data, "row_span", "WordTableCell", _wire_int, required=True)
        _put(out, data, "col_span", "WordTableCell", _wire_int, required=True)
        _put(out, data, "is_header", "WordTableCell", _wire_bool, required=True)
        _put(
            out,
            data,
            "is_merge_continuation",
            "WordTableCell",
            _wire_bool,
            required=True,
        )
        _put(out, data, "width", "WordTableCell", _wire_opt_int)
        _put(out, data, "runs", "WordTableCell", _wire_run_tuple, required=True)
        _put(out, data, "tables", "WordTableCell", _wire_table_tuple, required=True)
        return cls.model_validate(out)

    def to_wire(self) -> dict[str, Any]:
        """The cell's wire form (WIRE.md §2.7)."""
        return {
            "text": self.text,
            "row_span": self.row_span,
            "col_span": self.col_span,
            "is_header": self.is_header,
            "is_merge_continuation": self.is_merge_continuation,
            "width": self.width,
            "runs": [run.to_wire() for run in self.runs],
            "tables": [table.to_wire() for table in self.tables],
        }

    @model_validator(mode="after")
    def validate_merge_state(self) -> "WordTableCell":
        """A continuation cell cannot span rows and carries no text.

        The text rule is not cosmetic: a merged cell's content lives in the
        cell that *starts* the merge, and Word ignores whatever is stored in a
        continuation cell. Accepting text here would let the model carry
        content that no renderer can display — silently lost content, which is
        the defect this domain exists to eliminate.
        """
        if self.is_merge_continuation:
            if self.row_span != 1:
                raise ValueError(
                    "a merge continuation cell spans a single row, "
                    f"got row_span={self.row_span}"
                )
            if self.text:
                raise ValueError(
                    "a merge continuation cell carries no text of its own; "
                    "the text belongs to the cell that starts the merge"
                )
        return self


class WordTableRow(BaseModel):
    """One row of a Word table."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    cells: tuple[WordTableCell, ...] = Field(default=(), description="Cells left to right")

    @classmethod
    def from_wire(cls, payload: Mapping[str, Any]) -> "WordTableRow":
        """Build a table row from its wire form (WIRE.md §2.6).

        Raises:
            ValidationError: If a value is malformed.
        """
        data = _wire_mapping(payload, "WordTableRow")
        out: dict[str, Any] = {}
        _put(out, data, "cells", "WordTableRow", _wire_table_cell_tuple, required=True)
        return cls.model_validate(out)

    def to_wire(self) -> dict[str, Any]:
        """The row's wire form (WIRE.md §2.6)."""
        return {"cells": [cell.to_wire() for cell in self.cells]}


class WordTable(BaseModel):
    """A Word table with an optional repeating header."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rows: tuple[WordTableRow, ...] = Field(default=(), description="Rows top to bottom")
    header_rows: int = Field(
        default=0,
        ge=0,
        description="Number of leading rows that repeat as a header",
    )
    column_widths: tuple[int, ...] = Field(
        default=(),
        description="Width of every grid column, in twips",
    )

    @classmethod
    def from_wire(cls, payload: Mapping[str, Any]) -> "WordTable":
        """Build a table from its wire form (WIRE.md §2.5).

        Raises:
            ValidationError: If a value is malformed.
        """
        data = _wire_mapping(payload, "WordTable")
        out: dict[str, Any] = {}
        _put(out, data, "rows", "WordTable", _wire_table_row_tuple, required=True)
        _put(out, data, "header_rows", "WordTable", _wire_int, required=True)
        _put(out, data, "column_widths", "WordTable", _wire_int_tuple, required=True)
        return cls.model_validate(out)

    def to_wire(self) -> dict[str, Any]:
        """The table's wire form (WIRE.md §2.5)."""
        return {
            "rows": [row.to_wire() for row in self.rows],
            "header_rows": self.header_rows,
            "column_widths": list(self.column_widths),
        }


class WordBlock(BaseModel):
    """One ordered block of a Word document.

    The model is deliberately a single type with optional facets rather than a
    discriminated union: Word content mixes freely (a table inside a cell, a
    heading inside a content control) and the reader must be able to represent
    what it actually found without discarding it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    block_id: str = Field(min_length=1, description="Stable identifier within the document")
    kind: WordBlockKind = Field(description="Structural kind of the block")
    text: str = Field(default="", description="Normalised plain text of the block")
    level: int = Field(
        default=0,
        ge=0,
        description="Heading level (1-9) or list nesting depth (0-8)",
    )
    list_marker: Optional[ListMarker] = Field(
        default=None,
        description="Marker style for list items",
    )
    list_group: Optional[str] = Field(
        default=None,
        description="Continuity group for ordered numbering",
    )
    number_format: Optional[str] = Field(
        default=None,
        description="w:numFmt of the list level, as Word spells it",
    )
    number_text: Optional[str] = Field(
        default=None,
        description="w:lvlText of the list level, as Word spells it",
    )
    style: Optional[str] = Field(default=None, description="Source style name, when known")
    runs: tuple[WordRun, ...] = Field(default=(), description="Inline runs, in order")
    table: Optional[WordTable] = Field(default=None, description="Table payload for TABLE blocks")

    @classmethod
    def from_wire(cls, payload: Mapping[str, Any]) -> "WordBlock":
        """Build a block from its wire form (WIRE.md §2.2).

        Raises:
            ValidationError: If a required key is missing or a value is
                malformed.  A missing key is never answered with a default.
        """
        data = _wire_mapping(payload, "WordBlock")
        out: dict[str, Any] = {}
        _put(out, data, "block_id", "WordBlock", _wire_str, required=True)
        _put(out, data, "kind", "WordBlock", _wire_block_kind, required=True)
        _put(out, data, "text", "WordBlock", _wire_str, required=True)
        _put(out, data, "level", "WordBlock", _wire_int, required=True)
        _put(out, data, "list_marker", "WordBlock", _wire_list_marker)
        _put(out, data, "list_group", "WordBlock", _wire_opt_str)
        _put(out, data, "number_format", "WordBlock", _wire_opt_str)
        _put(out, data, "number_text", "WordBlock", _wire_opt_str)
        _put(out, data, "style", "WordBlock", _wire_opt_str)
        _put(out, data, "runs", "WordBlock", _wire_run_tuple, required=True)
        _put(out, data, "table", "WordBlock", _wire_opt_table)
        return cls.model_validate(out)

    def to_wire(self) -> dict[str, Any]:
        """The block's wire form (WIRE.md §2.2)."""
        return {
            "block_id": self.block_id,
            "kind": self.kind.value,
            "text": self.text,
            "level": self.level,
            "list_marker": self.list_marker.value if self.list_marker is not None else None,
            "list_group": self.list_group,
            "number_format": self.number_format,
            "number_text": self.number_text,
            "style": self.style,
            "runs": [run.to_wire() for run in self.runs],
            "table": self.table.to_wire() if self.table is not None else None,
        }

    @model_validator(mode="after")
    def validate_shape(self) -> "WordBlock":
        """Enforce the contract's §3.1 invariants."""
        if self.kind is WordBlockKind.TABLE:
            if self.table is None:
                raise ValueError("a TABLE block requires a table payload")
            if self.text or self.runs:
                raise ValueError("a TABLE block carries no inline text or runs")
        elif self.table is not None:
            raise ValueError(f"a {self.kind.value} block must not carry a table payload")

        if self.kind is WordBlockKind.LIST_ITEM:
            if self.list_marker is None:
                raise ValueError("a LIST_ITEM block requires a list marker")
        elif self.list_marker is not None:
            raise ValueError(f"a {self.kind.value} block must not carry a list marker")

        if self.kind is WordBlockKind.HEADING:
            if not 1 <= self.level <= 9:
                raise ValueError(f"a HEADING block requires a level between 1 and 9, got {self.level}")
        elif self.kind in {
            WordBlockKind.TITLE,
            WordBlockKind.PARAGRAPH,
            WordBlockKind.QUOTE,
            WordBlockKind.CODE,
        }:
            if self.level != 0:
                raise ValueError(f"a {self.kind.value} block must have level 0, got {self.level}")
        elif self.kind is WordBlockKind.LIST_ITEM and self.level > 8:
            raise ValueError(f"a LIST_ITEM block cannot nest deeper than 8, got {self.level}")

        if self.kind is WordBlockKind.PAGE_BREAK and self.text:
            raise ValueError("a PAGE_BREAK block carries no text")

        if self.runs:
            expected = normalize_whitespace(
                "".join(run.text for run in self.runs if not run.is_deleted)
            )
            if self.text != expected:
                raise ValueError(
                    "block text must equal the normalised concatenation of its "
                    "live runs "
                    f"({self.text!r} != {expected!r})"
                )
        return self

    def render_text(self) -> str:
        """Render the block as plain text for ingestion and round-trip comparison.

        Implements the contract's T5-T9: empty blocks render as nothing, tables
        render exactly one pipe-separated line per row — blank rows included —
        and list markers are omitted because they are structure rather than
        content.
        """
        if self.kind is WordBlockKind.TABLE:
            table = self.table
            if table is None:  # pragma: no cover - guarded by the validator
                return ""
            return "\n".join(
                " | ".join(cell.text for cell in row.cells) for row in table.rows
            )
        if self.kind is WordBlockKind.PAGE_BREAK:
            return ""
        return self.text


class WordProperties(BaseModel):
    """Document-level metadata, mapped from and to ``docProps/core.xml``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    title: str = Field(default="", description="Document title")
    author: str = Field(default="", description="Document author")
    subject: str = Field(default="", description="Document subject")
    keywords: str = Field(default="", description="Document keywords")
    comments: str = Field(default="", description="Document comments")
    created: Optional[datetime] = Field(default=None, description="Creation timestamp")
    modified: Optional[datetime] = Field(default=None, description="Last modification timestamp")
    revision: int = Field(default=1, ge=1, description="Revision number")

    @classmethod
    def from_wire(cls, payload: Mapping[str, Any]) -> "WordProperties":
        """Build the core properties from their wire form (WIRE.md §2.16).

        Raises:
            ValidationError: If a value is malformed.
        """
        data = _wire_mapping(payload, "WordProperties")
        out: dict[str, Any] = {}
        _put(out, data, "title", "WordProperties", _wire_str, required=True)
        _put(out, data, "author", "WordProperties", _wire_str, required=True)
        _put(out, data, "subject", "WordProperties", _wire_str, required=True)
        _put(out, data, "keywords", "WordProperties", _wire_str, required=True)
        _put(out, data, "comments", "WordProperties", _wire_str, required=True)
        _put(out, data, "created", "WordProperties", _wire_datetime)
        _put(out, data, "modified", "WordProperties", _wire_datetime)
        _put(out, data, "revision", "WordProperties", _wire_int, required=True)
        return cls.model_validate(out)

    def to_wire(self) -> dict[str, Any]:
        """The properties' wire form (WIRE.md §2.16)."""
        return {
            "title": self.title,
            "author": self.author,
            "subject": self.subject,
            "keywords": self.keywords,
            "comments": self.comments,
            "created": _wire_timestamp(self.created),
            "modified": _wire_timestamp(self.modified),
            "revision": self.revision,
        }


class WordDocument(BaseModel):
    """A Word document as a flat, ordered sequence of blocks."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    blocks: tuple[WordBlock, ...] = Field(default=(), description="Blocks in document order")
    properties: WordProperties = Field(default_factory=WordProperties, description="Core properties")
    footnotes: tuple[WordFootnote, ...] = Field(default=(), description="Footnotes by id")
    endnotes: tuple[WordEndnote, ...] = Field(default=(), description="Endnotes by id")
    comments: tuple[WordComment, ...] = Field(default=(), description="Comments by id")
    headers: tuple[str, ...] = Field(default=(), description="Header texts, in part order")
    footers: tuple[str, ...] = Field(default=(), description="Footer texts, in part order")
    sections: tuple[WordSection, ...] = Field(default=(), description="Sections, in document order")
    styles: tuple[WordStyle, ...] = Field(default=(), description="Style definitions")
    media: tuple[WordMedia, ...] = Field(default=(), description="Embedded media parts")
    parts: tuple[WordPart, ...] = Field(
        default=(),
        description="Package parts carried through without modelling their content",
    )

    @classmethod
    def from_wire(cls, payload: Mapping[str, Any]) -> "WordDocument":
        """Build a document from its wire form (WIRE.md §2.1).

        WIRE.md §2.1 says every key of a document is always present, so all
        eleven of them are required here: a document that arrives without one
        is a contract violation, not a document to be filled in with defaults.

        Raises:
            ValidationError: If a required key is missing or a value is
                malformed.  A missing key is never answered with a default.
        """
        data = _wire_mapping(payload, "WordDocument")
        out: dict[str, Any] = {}
        _put(out, data, "blocks", "WordDocument", _wire_block_tuple, required=True)
        _put(out, data, "properties", "WordDocument", _wire_properties, required=True)
        _put(out, data, "footnotes", "WordDocument", _wire_footnote_tuple, required=True)
        _put(out, data, "endnotes", "WordDocument", _wire_endnote_tuple, required=True)
        _put(out, data, "comments", "WordDocument", _wire_comment_tuple, required=True)
        _put(out, data, "headers", "WordDocument", _wire_str_tuple, required=True)
        _put(out, data, "footers", "WordDocument", _wire_str_tuple, required=True)
        _put(out, data, "sections", "WordDocument", _wire_section_tuple, required=True)
        _put(out, data, "styles", "WordDocument", _wire_style_tuple, required=True)
        _put(out, data, "media", "WordDocument", _wire_media_tuple, required=True)
        _put(out, data, "parts", "WordDocument", _wire_part_tuple, required=True)
        return cls.model_validate(out)

    def to_wire(self) -> dict[str, Any]:
        """The document's wire form (WIRE.md §2.1)."""
        return {
            "blocks": [block.to_wire() for block in self.blocks],
            "properties": self.properties.to_wire(),
            "footnotes": [footnote.to_wire() for footnote in self.footnotes],
            "endnotes": [endnote.to_wire() for endnote in self.endnotes],
            "comments": [comment.to_wire() for comment in self.comments],
            "headers": list(self.headers),
            "footers": list(self.footers),
            "sections": [section.to_wire() for section in self.sections],
            "styles": [style.to_wire() for style in self.styles],
            "media": [media.to_wire() for media in self.media],
            "parts": [part.to_wire() for part in self.parts],
        }

    @model_validator(mode="after")
    def validate_document(self) -> "WordDocument":
        """Enforce the contract's §3.2 invariants."""
        seen: set[str] = set()
        for block in self.blocks:
            if block.block_id in seen:
                raise ValueError(f"duplicate block_id {block.block_id!r}")
            seen.add(block.block_id)

        known_footnotes = {footnote.footnote_id for footnote in self.footnotes}
        known_endnotes = {endnote.endnote_id for endnote in self.endnotes}
        known_comments = {comment.comment_id for comment in self.comments}
        for block in self.blocks:
            for run in block.runs:
                if run.footnote_ref is not None and run.footnote_ref not in known_footnotes:
                    raise ValueError(
                        f"run references unknown footnote {run.footnote_ref!r}"
                    )
                if run.endnote_ref is not None and run.endnote_ref not in known_endnotes:
                    raise ValueError(
                        f"run references unknown endnote {run.endnote_ref!r}"
                    )
                for comment_id in run.comment_refs:
                    if comment_id not in known_comments:
                        raise ValueError(
                            f"run references unknown comment {comment_id!r}"
                        )
        return self

    def plain_text(self) -> str:
        """Render the whole document as plain text.

        Implements the contract's T6: surviving blocks are joined with a single
        blank line. The result is deterministic, which is what makes the
        round-trip property testable.
        """
        rendered = [block.render_text() for block in self.blocks]
        return "\n\n".join(part for part in rendered if part.strip())


# ``WordTableCell`` refers to ``WordTable`` and ``WordTable`` refers back to
# it, so the forward reference has to be resolved once both exist.
WordTableCell.model_rebuild()
WordTable.model_rebuild()


__all__ = [
    "ListMarker",
    "WordBlock",
    "WordBlockKind",
    "WordComment",
    "WordDocument",
    "WordEndnote",
    "WordFootnote",
    "WordHeaderFooterRef",
    "WordMedia",
    "WordPart",
    "WordPartRelationship",
    "WordProperties",
    "WordRevision",
    "WordRevisionKind",
    "WordRun",
    "WordSection",
    "WordStyle",
    "WordTable",
    "WordTableCell",
    "WordTableRow",
]
