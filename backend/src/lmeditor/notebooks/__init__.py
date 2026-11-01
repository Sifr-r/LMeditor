"""The notebook: a named collection of sources and notes on the operator's disk.

A notebook is not a format LMeditor owns. It is a real folder on the
operator's machine, with a manifest in it, and that is what makes this an
Open Notebook replacement rather than a second application with the same job:
the material the operator gathered stays where they put it, and the questions
they ask of it are answered from that material and nowhere else.

Every rule that decides anything about a notebook belongs to the Kotlin tier:
the identifiers that are safe to write, the shape of the manifest, the counts,
the ordering, the idempotence of a write and the membership of a source or a
note. This package is the Python API over that tier. It asks the door
(:mod:`lmeditor.tier.notebooks`) and hands back the tier's answers as the
models below, so that the rest of the backend never has to know in which
language the rules live -- and never gets to keep a copy of them.

The models here mirror the Kotlin core models field for field
(``kotlin/core/NotebookSchemas.kt``): the same names, the same defaults and
the same meaning, because the desktop client reads what this side writes with
the Kotlin decoders.
"""

from __future__ import annotations

from typing import Annotated, Any, TypeVar

from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from pydantic import ValidationError as PydanticValidationError

from lmeditor.bridge.errors import BridgeProtocolError
from lmeditor.core.exceptions import NotebookError
from lmeditor.tier import notebooks as tier_notebooks

_ModelT = TypeVar("_ModelT", bound=BaseModel)

#: A string the way the Kotlin tier's models hold one: the whitespace around
#: it is removed before the constraints are checked, so a field that is
#: nothing but whitespace is empty.
StrippedStr = Annotated[str, StringConstraints(strip_whitespace=True)]


class Notebook(BaseModel):
    """One notebook: the sources it holds, the notes it refers to, and when it
    was last changed.

    A notebook *points at* its sources and notes; it does not own them.
    Removing a source from a notebook leaves the source where it is, because
    the source is the operator's and the notebook only borrows it.
    """

    model_config = ConfigDict(extra="ignore")

    notebook_id: StrippedStr = Field(min_length=1)
    title: StrippedStr = ""
    description: StrippedStr = ""
    source_ids: list[StrippedStr] = Field(default_factory=list)
    note_ids: list[StrippedStr] = Field(default_factory=list)
    created_at: StrippedStr = ""
    modified_at: StrippedStr = ""

    def to_wire(self) -> dict[str, Any]:
        """The wire form of this notebook.

        The counterpart of the Kotlin tier's ``Notebook.toWireMap()``: the same
        keys, the same names and the same defaults, so the desktop client can
        read it with the Kotlin decoders.
        """
        return {
            "notebook_id": self.notebook_id,
            "title": self.title,
            "description": self.description,
            "source_ids": list(self.source_ids),
            "note_ids": list(self.note_ids),
            "created_at": self.created_at,
            "modified_at": self.modified_at,
        }


class NotebookSummary(BaseModel):
    """What the client needs to list a notebook without reading it whole.

    The summary is the notebook's index entry: its title, what it is for, and
    how much material it holds.
    """

    model_config = ConfigDict(extra="ignore")

    notebook_id: StrippedStr = Field(min_length=1)
    title: StrippedStr = ""
    description: StrippedStr = ""
    source_count: int = Field(default=0, ge=0)
    note_count: int = Field(default=0, ge=0)
    modified_at: StrippedStr = ""

    def to_wire(self) -> dict[str, Any]:
        """The wire form of this summary.

        The counterpart of the Kotlin tier's ``NotebookSummary.toWireMap()``.
        """
        return {
            "notebook_id": self.notebook_id,
            "title": self.title,
            "description": self.description,
            "source_count": self.source_count,
            "note_count": self.note_count,
            "modified_at": self.modified_at,
        }


class NotebookCitation(BaseModel):
    """One passage an answer leaned on.

    A citation is a claim about what the machine did, so it names the passage
    it came from, the source it belongs to, the source's title, and the
    passage's own words. The quote is the passage's text verbatim, so the
    operator can read the evidence rather than trust it.
    """

    model_config = ConfigDict(extra="ignore")

    chunk_id: StrippedStr = Field(min_length=1)
    source_id: StrippedStr = Field(min_length=1)
    title: StrippedStr = ""
    quote: str = ""

    def to_wire(self) -> dict[str, Any]:
        """The wire form of this citation.

        The counterpart of the Kotlin tier's ``NotebookCitation.toWireMap()``.
        """
        return {
            "chunk_id": self.chunk_id,
            "source_id": self.source_id,
            "title": self.title,
            "quote": self.quote,
        }


class NotebookAnswer(BaseModel):
    """The answer to one question about one notebook.

    The answer is ``answered`` only when it leans on at least one passage of
    the notebook's own sources. An answer that says the sources do not answer
    the question is a successful ask with ``answered`` false; an answer that
    invents one is not an answer at all.
    """

    model_config = ConfigDict(extra="ignore")

    answer: str = ""
    answered: bool = False
    citations: list[NotebookCitation] = Field(default_factory=list)

    def to_wire(self) -> dict[str, Any]:
        """The wire form of this answer.

        The counterpart of the Kotlin tier's ``NotebookAnswer.toWireMap()``.
        """
        return {
            "answer": self.answer,
            "answered": self.answered,
            "citations": [citation.to_wire() for citation in self.citations],
        }


class NotebookLibrary(BaseModel):
    """The notebooks folder the server holds, and the notebooks in it.

    The folder is the operator's own directory of notebooks. The client shows
    where it is, so the operator always knows which folder their work is in.
    """

    model_config = ConfigDict(extra="ignore")

    root: StrippedStr = ""
    name: StrippedStr = ""
    notebook_count: int = Field(default=0, ge=0)
    notebooks: list[NotebookSummary] = Field(default_factory=list)

    def to_wire(self) -> dict[str, Any]:
        """The wire form of this folder.

        The counterpart of the Kotlin tier's ``NotebookLibrary.toWireMap()``:
        the client reads the root, the name, the count and the notebooks from
        it, so a list never has to guess how many notebooks there are.
        """
        return {
            "root": self.root,
            "name": self.name,
            "notebook_count": self.notebook_count,
            "notebooks": [notebook.to_wire() for notebook in self.notebooks],
        }


def _validated(model: type[_ModelT], payload: dict[str, Any]) -> _ModelT:
    """The tier's answer as a model, or a refusal when it is not one.

    An answer that does not match the contract is a broken contract, not a
    bad request, so it is reported as one and never as an empty notebook.
    """
    try:
        return model.model_validate(payload)
    except PydanticValidationError as exc:
        raise BridgeProtocolError(
            f"the notebook answer does not match the contract: {model.__name__}",
            str(exc),
        ) from exc


def list_notebooks(root: str) -> list[NotebookSummary]:
    """Every notebook in the folder, as a summary."""
    return [
        _validated(NotebookSummary, item) for item in tier_notebooks.list_notebooks(root)
    ]


def read_notebook(root: str, notebook_id: str) -> Notebook:
    """One notebook, with the sources it holds and the notes it refers to."""
    return _validated(Notebook, tier_notebooks.read_notebook(root, notebook_id))


def create_notebook(
    root: str,
    title: str,
    description: str = "",
    notebook_id: str | None = None,
) -> Notebook:
    """Create a notebook and return it as it was written.

    Creating a notebook that already exists returns it unchanged, so the
    call is idempotent.
    """
    return _validated(
        Notebook,
        tier_notebooks.create_notebook(root, title, description, notebook_id),
    )


def set_notebook_metadata(
    root: str,
    notebook_id: str,
    title: str,
    description: str = "",
) -> Notebook:
    """Rename a notebook and say what it is for."""
    return _validated(
        Notebook,
        tier_notebooks.set_notebook_metadata(root, notebook_id, title, description),
    )


def add_notebook_source(root: str, notebook_id: str, source_id: str) -> Notebook:
    """Hold a source in a notebook. The source itself is not touched."""
    return _validated(
        Notebook,
        tier_notebooks.add_source(root, notebook_id, source_id),
    )


def remove_notebook_source(root: str, notebook_id: str, source_id: str) -> Notebook:
    """Stop holding a source. The source stays in the library."""
    return _validated(
        Notebook,
        tier_notebooks.remove_source(root, notebook_id, source_id),
    )


def add_notebook_note(root: str, notebook_id: str, note_id: str) -> Notebook:
    """Refer to a note from a notebook."""
    return _validated(
        Notebook,
        tier_notebooks.add_note(root, notebook_id, note_id),
    )


def remove_notebook_note(root: str, notebook_id: str, note_id: str) -> Notebook:
    """Stop referring to a note."""
    return _validated(
        Notebook,
        tier_notebooks.remove_note(root, notebook_id, note_id),
    )


def delete_notebook(root: str, notebook_id: str) -> bool:
    """Delete a notebook. The sources and notes it pointed at are not."""
    return tier_notebooks.delete_notebook(root, notebook_id)


# The Ask lives in its own module, and it is imported here at the bottom
# because that module reads the models above: importing it earlier would ask
# for a model that is not defined yet.
from lmeditor.notebooks.answer import ask_notebook  # noqa: E402

__all__ = [
    "Notebook",
    "NotebookAnswer",
    "NotebookCitation",
    "NotebookError",
    "NotebookLibrary",
    "NotebookSummary",
    "add_notebook_note",
    "add_notebook_source",
    "ask_notebook",
    "create_notebook",
    "delete_notebook",
    "list_notebooks",
    "read_notebook",
    "remove_notebook_note",
    "remove_notebook_source",
    "set_notebook_metadata",
]
