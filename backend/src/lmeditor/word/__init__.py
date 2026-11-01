"""Word (OOXML / ``.docx``) processing domain.

This package owns LMeditor's word-processing element in both directions:

* :mod:`lmeditor.word.reader` maps a ``.docx`` package onto the shared model.
* :mod:`lmeditor.word.writer` maps the shared model back onto a ``.docx`` package.
* :mod:`lmeditor.word.manuscript` builds the shared model from LMeditor's own
  manuscript domain models.

The model in :mod:`lmeditor.word.model` is the contract between all three, so
structure that survives one direction survives the other. The package depends
only on ``lmeditor.core`` and the standard library.

Submodules are exposed lazily: importing :mod:`lmeditor.word.model` must not
require the OOXML machinery of the reader and the writer, and a caller that
only needs the model should not pay for the rest.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:  # pragma: no cover - import-time only for type checkers
    # The ``as`` aliases are what make these explicit re-exports. Under
    # ``--strict`` a name imported into a module is not re-exported unless it
    # is aliased or listed in a literal ``__all__``; the lazy loader's
    # ``__all__`` is computed at runtime, so a type checker cannot read it.
    # Without the aliases ``from lmeditor.word import WordReader`` is a type
    # error even though it works at run time.
    from lmeditor.word.formats import (
        LEGACY_DOC_REASON as LEGACY_DOC_REASON,
    )
    from lmeditor.word.formats import (
        describe_unsupported_format as describe_unsupported_format,
    )
    from lmeditor.word.formats import is_legacy_doc as is_legacy_doc
    from lmeditor.word.manuscript import (
        build_manuscript_document as build_manuscript_document,
    )
    from lmeditor.word.model import (
        ListMarker as ListMarker,
    )
    from lmeditor.word.model import (
        WordBlock as WordBlock,
    )
    from lmeditor.word.model import (
        WordBlockKind as WordBlockKind,
    )
    from lmeditor.word.model import (
        WordComment as WordComment,
    )
    from lmeditor.word.model import (
        WordDocument as WordDocument,
    )
    from lmeditor.word.model import (
        WordEndnote as WordEndnote,
    )
    from lmeditor.word.model import (
        WordFootnote as WordFootnote,
    )
    from lmeditor.word.model import (
        WordMedia as WordMedia,
    )
    from lmeditor.word.model import (
        WordPart as WordPart,
    )
    from lmeditor.word.model import (
        WordPartRelationship as WordPartRelationship,
    )
    from lmeditor.word.model import (
        WordProperties as WordProperties,
    )
    from lmeditor.word.model import (
        WordRevision as WordRevision,
    )
    from lmeditor.word.model import (
        WordRevisionKind as WordRevisionKind,
    )
    from lmeditor.word.model import (
        WordRun as WordRun,
    )
    from lmeditor.word.model import (
        WordSection as WordSection,
    )
    from lmeditor.word.model import (
        WordStyle as WordStyle,
    )
    from lmeditor.word.model import (
        WordTable as WordTable,
    )
    from lmeditor.word.model import (
        WordTableCell as WordTableCell,
    )
    from lmeditor.word.model import (
        WordTableRow as WordTableRow,
    )
    from lmeditor.word.reader import WordReader as WordReader
    from lmeditor.word.text import normalize_whitespace as normalize_whitespace
    from lmeditor.word.writer import WordWriter as WordWriter

#: Public name -> (submodule, attribute) used by the lazy loader below.
_EXPORTS: dict[str, tuple[str, str]] = {
    "LEGACY_DOC_REASON": ("lmeditor.word.formats", "LEGACY_DOC_REASON"),
    "ListMarker": ("lmeditor.word.model", "ListMarker"),
    "WordBlock": ("lmeditor.word.model", "WordBlock"),
    "WordBlockKind": ("lmeditor.word.model", "WordBlockKind"),
    "WordComment": ("lmeditor.word.model", "WordComment"),
    "WordDocument": ("lmeditor.word.model", "WordDocument"),
    "WordEndnote": ("lmeditor.word.model", "WordEndnote"),
    "WordFootnote": ("lmeditor.word.model", "WordFootnote"),
    "WordMedia": ("lmeditor.word.model", "WordMedia"),
    "WordPart": ("lmeditor.word.model", "WordPart"),
    "WordPartRelationship": ("lmeditor.word.model", "WordPartRelationship"),
    "WordProperties": ("lmeditor.word.model", "WordProperties"),
    "WordReader": ("lmeditor.word.reader", "WordReader"),
    "WordRevision": ("lmeditor.word.model", "WordRevision"),
    "WordRevisionKind": ("lmeditor.word.model", "WordRevisionKind"),
    "WordRun": ("lmeditor.word.model", "WordRun"),
    "WordSection": ("lmeditor.word.model", "WordSection"),
    "WordStyle": ("lmeditor.word.model", "WordStyle"),
    "WordTable": ("lmeditor.word.model", "WordTable"),
    "WordTableCell": ("lmeditor.word.model", "WordTableCell"),
    "WordTableRow": ("lmeditor.word.model", "WordTableRow"),
    "WordWriter": ("lmeditor.word.writer", "WordWriter"),
    "build_manuscript_document": ("lmeditor.word.manuscript", "build_manuscript_document"),
    "describe_unsupported_format": (
        "lmeditor.word.formats",
        "describe_unsupported_format",
    ),
    "is_legacy_doc": ("lmeditor.word.formats", "is_legacy_doc"),
    "normalize_whitespace": ("lmeditor.word.text", "normalize_whitespace"),
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    """Resolve a public name to its submodule attribute on first access."""
    try:
        module_name, attribute = _EXPORTS[name]
    except KeyError:
        raise AttributeError(
            f"module {__name__!r} has no attribute {name!r}"
        ) from None
    module = import_module(module_name)
    value = getattr(module, attribute)
    globals()[name] = value
    return cast(Any, value)


def __dir__() -> list[str]:
    """Expose the public names to interactive tooling."""
    return sorted(set(globals()) | set(_EXPORTS))
