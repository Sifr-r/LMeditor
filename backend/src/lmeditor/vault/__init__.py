"""The vault: a folder of markdown files on the operator's disk.

A vault is not a format LMeditor owns. It is a real folder, and a note is a
real markdown file: that is what makes this an Obsidian replacement rather
than a second application with the same job. The operator's existing vault
opens, Obsidian still opens what this application writes, and the notes stay
greppable on disk.

Every rule that decides anything about a vault belongs to the Kotlin tier:
the path safety, the frontmatter, the link and tag grammar, the backlink
index and the search order. This package is the Python API over that tier.
It asks the door (:mod:`lmeditor.tier.vault`) and hands back the tier's
answers as the models below, so that the rest of the backend never has to
know in which language the rules live -- and never gets to keep a copy of
them.
"""

from __future__ import annotations

from typing import Any, TypeVar

from pydantic import BaseModel, ConfigDict, Field
from pydantic import ValidationError as PydanticValidationError

from lmeditor.bridge.errors import BridgeProtocolError
from lmeditor.core.exceptions import VaultError
from lmeditor.tier import vault as tier_vault

_ModelT = TypeVar("_ModelT", bound=BaseModel)


class VaultNoteSummary(BaseModel):
    """One note as the list shows it: enough to navigate by, never the body.

    The answers carry more than the contract names sometimes; the extra
    fields are dropped rather than carried, so the shape a client sees is
    the shape the contract fixes.
    """

    model_config = ConfigDict(extra="ignore")

    note_id: str
    title: str
    path: str
    tags: list[str] = Field(default_factory=list)
    word_count: int = 0
    modified_at: str = ""


class VaultNote(BaseModel):
    """One note, with the links it carries and the notes that link back to it."""

    model_config = ConfigDict(extra="ignore")

    note_id: str
    title: str
    path: str
    content: str = ""
    tags: list[str] = Field(default_factory=list)
    links: list[str] = Field(default_factory=list)
    backlinks: list[str] = Field(default_factory=list)
    created_at: str = ""
    modified_at: str = ""
    word_count: int = 0


class VaultSearchHit(BaseModel):
    """One search hit: the note, the terms it matched and how strongly."""

    model_config = ConfigDict(extra="ignore")

    note_id: str
    title: str
    path: str
    score: float = 0.0
    matched_terms: list[str] = Field(default_factory=list)


class VaultTag(BaseModel):
    """One tag and the number of notes that carry it."""

    model_config = ConfigDict(extra="ignore")

    tag: str
    count: int = 0


def _validated(model: type[_ModelT], payload: dict[str, Any]) -> _ModelT:
    """The tier's answer as a model, or a refusal when it is not one.

    An answer that does not match the contract is a broken contract, not a
    bad request, so it is reported as one and never as an empty note.
    """
    try:
        return model.model_validate(payload)
    except PydanticValidationError as exc:
        raise BridgeProtocolError(
            f"the vault answer does not match the contract: {model.__name__}",
            str(exc),
        ) from exc


def list_notes(root: str) -> list[VaultNoteSummary]:
    """Every note in the vault, as a summary."""
    return [_validated(VaultNoteSummary, item) for item in tier_vault.list_notes(root)]


def read_note(root: str, note_id: str) -> VaultNote:
    """One note, with its links and the notes that link back to it."""
    return _validated(VaultNote, tier_vault.read_note(root, note_id))


def write_note(root: str, note_id: str, content: str) -> VaultNote:
    """Store the operator's own text for one note."""
    return _validated(VaultNote, tier_vault.write_note(root, note_id, content))


def create_note(root: str, note_id: str, title: str, content: str) -> VaultNote:
    """Create a note and return it as it was written."""
    return _validated(VaultNote, tier_vault.create_note(root, note_id, title, content))


def delete_note(root: str, note_id: str) -> bool:
    """Remove a note from the vault."""
    return tier_vault.delete_note(root, note_id)


def rename_note(root: str, from_id: str, to_id: str, update_links: bool) -> dict[str, Any]:
    """Move a note, and rewrite the links that point at it when asked."""
    return tier_vault.rename_note(root, from_id, to_id, update_links)


def search(root: str, query: str, limit: int = 50) -> list[VaultSearchHit]:
    """The ranked search over the vault."""
    return [_validated(VaultSearchHit, item) for item in tier_vault.search(root, query, limit)]


def tags(root: str) -> list[VaultTag]:
    """Every tag in the vault, with the number of notes that carry it."""
    return [_validated(VaultTag, item) for item in tier_vault.tags(root)]


__all__ = [
    "VaultError",
    "VaultNote",
    "VaultNoteSummary",
    "VaultSearchHit",
    "VaultTag",
    "create_note",
    "delete_note",
    "list_notes",
    "read_note",
    "rename_note",
    "search",
    "tags",
    "write_note",
]
