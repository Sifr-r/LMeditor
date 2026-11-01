"""The vault rules, as the Kotlin tier implements them.

A vault is a folder of markdown files on the operator's disk: a note is a
file, a wiki-link is a wiki-link and a tag is a tag. Every rule that decides
anything about them -- the path safety, the frontmatter, the link and tag
grammar, the backlink index and the search order -- belongs to the Kotlin
tier, and this module keeps no copy of any of them.

This module is the **door** over the seam: it keeps the exact Python API
the rest of the backend calls, and answers with the tier's answers. The
root travels with every request, because the tier keeps no state of its
own: the server holds the active vault and hands the root over on every
call, so a caller that works in one vault never touches another.

A refusal keeps its meaning. The tier's own domain errors arrive as the
seam's error type, and they become the Python errors the routes know about:
a note that is not there, and a request the tier refused. A failure of the
seam itself stays the infrastructure failure it is -- it is never dressed up
as a bad input, and it is never an empty answer that looks like success.
"""

from __future__ import annotations

from typing import Any

from lmeditor.bridge.errors import BridgeUnavailableError, KotlinDomainError
from lmeditor.core.exceptions import VaultError, VaultNotFoundError
from lmeditor.tier.bridge import tier_bridge

#: The seam's own error type. It is not a domain refusal: it means the tier
#: could not serve the method at all -- it does not know it, or it failed
#: in a way that is not one of its own typed errors.
_BRIDGE_ERROR_TYPE = "BridgeError"


def _call(method: str, params: dict[str, Any]) -> dict[str, Any]:
    """Ask the tier a question about the vault.

    A refusal of the request becomes a vault error that says what was
    wrong; a failure of the seam stays the infrastructure failure it is.
    """
    try:
        return tier_bridge().call(method, params)
    except KotlinDomainError as exc:
        if exc.error_type == _BRIDGE_ERROR_TYPE:
            raise BridgeUnavailableError(
                f"the Kotlin tier cannot serve '{method}'",
                exc.message,
            ) from exc
        raise _refusal(exc, method) from exc


def _refusal(exc: KotlinDomainError, method: str) -> VaultError:
    """The vault error a tier refusal becomes.

    The tier's message says what was wrong and is kept as it is, so the
    refusal keeps its meaning on this side of the seam. The tier's error
    type is kept in the details, so a caller can tell a refusal of the
    request from a note that is not there.
    """
    details: dict[str, Any] = dict(exc.details)
    details.setdefault("error_type", exc.error_type)
    details.setdefault("method", method)
    if _is_missing(exc):
        return VaultNotFoundError(exc.message, details=details)
    return VaultError(exc.message, details=details)


def _is_missing(exc: KotlinDomainError) -> bool:
    """Whether the tier refused because the note is not there.

    The contract fixes the answers, not the tier's error type names, so a
    refusal that says a note is missing is recognised by its type and by
    its message. A refusal the door does not recognise is a refusal of the
    request, and is reported as one.
    """
    if "notfound" in exc.error_type.lower():
        return True
    return "not found" in exc.message.lower()


def _object(result: dict[str, Any], key: str) -> dict[str, Any]:
    """The one object the tier's answer carries under ``key``."""
    value = result.get(key)
    if not isinstance(value, dict):
        return {}
    return {str(name): item for name, item in value.items()}


def _objects(result: dict[str, Any], key: str) -> list[dict[str, Any]]:
    """The list of objects the tier's answer carries under ``key``."""
    value = result.get(key)
    if not isinstance(value, list):
        return []
    objects: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, dict):
            objects.append({str(name): entry for name, entry in item.items()})
    return objects


def list_notes(root: str) -> list[dict[str, Any]]:
    """Every note in the vault, as a summary: enough to navigate by."""
    return _objects(_call("vault.list_notes", {"root": root}), "notes")


def read_note(root: str, note_id: str) -> dict[str, Any]:
    """One note, with the links it carries and the notes that link back to it."""
    return _object(_call("vault.read_note", {"root": root, "note_id": note_id}), "note")


def write_note(root: str, note_id: str, content: str) -> dict[str, Any]:
    """Store the operator's own text for one note."""
    return _object(
        _call("vault.write_note", {"root": root, "note_id": note_id, "content": content}),
        "note",
    )


def create_note(root: str, note_id: str, title: str, content: str) -> dict[str, Any]:
    """Create a note and return it as it was written."""
    return _object(
        _call(
            "vault.create_note",
            {"root": root, "note_id": note_id, "title": title, "content": content},
        ),
        "note",
    )


def delete_note(root: str, note_id: str) -> bool:
    """Remove a note from the vault."""
    result = _call("vault.delete_note", {"root": root, "note_id": note_id})
    return bool(result.get("deleted", False))


def rename_note(root: str, from_id: str, to_id: str, update_links: bool) -> dict[str, Any]:
    """Move a note, and rewrite the links that point at it when asked.

    The answer carries the two ids and the number of links that were
    rewritten, so the caller can tell what the move changed.
    """
    result = _call(
        "vault.rename_note",
        {
            "root": root,
            "from_id": from_id,
            "to_id": to_id,
            "update_links": update_links,
        },
    )
    updated = result.get("updated_links")
    return {
        "from_id": str(result.get("from_id", from_id)),
        "to_id": str(result.get("to_id", to_id)),
        "updated_links": int(updated) if isinstance(updated, (int, float)) else 0,
    }


def search(root: str, query: str, limit: int) -> list[dict[str, Any]]:
    """The ranked search over the vault."""
    return _objects(_call("vault.search", {"root": root, "query": query, "limit": limit}), "results")


def tags(root: str) -> list[dict[str, Any]]:
    """Every tag in the vault, with the number of notes that carry it."""
    return _objects(_call("vault.tags", {"root": root}), "tags")


__all__ = [
    "create_note",
    "delete_note",
    "list_notes",
    "read_note",
    "rename_note",
    "search",
    "tags",
    "write_note",
]
