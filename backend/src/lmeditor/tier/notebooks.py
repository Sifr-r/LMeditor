"""The notebook rules, as the Kotlin tier implements them.

A notebook is a named collection of sources and notes on the operator's disk:
a directory with a manifest, the way a book is kept in the library. Every rule
that decides anything about one -- the identifiers that are safe to write, the
shape of the manifest, the counts, the ordering, the idempotence of a write,
the membership of a source or a note -- belongs to the Kotlin tier, and this
module keeps no copy of any of them.

This module is the **door** over the seam: it keeps the exact Python API the
rest of the backend calls, and answers with the tier's answers. The root
travels with every request, because the tier keeps no state of its own: the
server holds the notebooks folder and hands the root over on every call, so a
caller that works in one folder never touches another.

A refusal keeps its meaning. The tier's own domain errors arrive as the seam's
error type, and they become the Python errors the routes know about: a
notebook that is not there, and a request the tier refused. A failure of the
seam itself stays the infrastructure failure it is -- it is never dressed up
as a bad input, and it is never an empty answer that looks like success.
"""

from __future__ import annotations

from typing import Any

from lmeditor.bridge.errors import BridgeUnavailableError, KotlinDomainError
from lmeditor.core.exceptions import NotebookError, NotebookNotFoundError
from lmeditor.tier.bridge import tier_bridge

#: The seam's own error type. It is not a domain refusal: it means the tier
#: could not serve the method at all -- it does not know it, or it failed
#: in a way that is not one of its own typed errors.
_BRIDGE_ERROR_TYPE = "BridgeError"


def _call(method: str, params: dict[str, Any]) -> dict[str, Any]:
    """Ask the tier a question about the notebooks.

    A refusal of the request becomes a notebook error that says what was
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


def _refusal(exc: KotlinDomainError, method: str) -> NotebookError:
    """The notebook error a tier refusal becomes.

    The tier's message says what was wrong and is kept as it is, so the
    refusal keeps its meaning on this side of the seam. The tier's error
    type is kept in the details, so a caller can tell a refusal of the
    request from a notebook that is not there.
    """
    details: dict[str, Any] = dict(exc.details)
    details.setdefault("error_type", exc.error_type)
    details.setdefault("method", method)
    if _is_missing(exc):
        return NotebookNotFoundError(exc.message, details=details)
    return NotebookError(exc.message, details=details)


def _is_missing(exc: KotlinDomainError) -> bool:
    """Whether the tier refused because the notebook is not there.

    The contract fixes the answers, not the tier's error type names, so a
    refusal that says a notebook is missing is recognised by its type and by
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


def _require_text(value: Any, name: str, method: str) -> str:
    """The argument as text, or a refusal when it is not usable.

    This is the door's own boundary check and nothing more: it says that the
    argument is a string with something in it. What a notebook id may and may
    not look like, what a title must derive an id from, and whether a source
    or a note is in the notebook are the tier's rules, and they are asked
    there.
    """
    if not isinstance(value, str):
        raise NotebookError(
            f"the {name} of a notebook request must be text.",
            reason=f"the {name} is not text",
            details={"method": method},
        )
    if not value.strip():
        raise NotebookError(
            f"the {name} of a notebook request must not be empty.",
            reason=f"the {name} is empty",
            details={"method": method},
        )
    return value


def _optional_text(value: Any, name: str, method: str) -> str | None:
    """The argument as text when the caller chose one, and ``None`` when they did not.

    An id that was left out and an id that is empty mean the same thing to
    the tier -- the id is derived from the title -- so the key is left out of
    the call entirely rather than sent as a placeholder.
    """
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return _require_text(value, name, method)


def list_notebooks(root: str) -> list[dict[str, Any]]:
    """Every notebook in the folder, as a summary: enough to navigate by."""
    _require_text(root, "root", "notebooks.list")
    return _objects(_call("notebooks.list", {"root": root}), "notebooks")


def read_notebook(root: str, notebook_id: str) -> dict[str, Any]:
    """One notebook, with the sources it holds and the notes it refers to."""
    _require_text(root, "root", "notebooks.read")
    _require_text(notebook_id, "notebook id", "notebooks.read")
    return _object(
        _call("notebooks.read", {"root": root, "notebook_id": notebook_id}),
        "notebook",
    )


def create_notebook(
    root: str,
    title: str,
    description: str = "",
    notebook_id: str | None = None,
) -> dict[str, Any]:
    """Create a notebook and return it as it was written.

    A request that names no id gets one derived from the title, so the id is
    left out of the call entirely when the caller did not choose one.
    """
    _require_text(root, "root", "notebooks.create")
    _require_text(title, "title", "notebooks.create")
    if description is not None and not isinstance(description, str):
        raise NotebookError(
            "the description of a notebook request must be text.",
            reason="the description is not text",
            details={"method": "notebooks.create"},
        )
    params: dict[str, Any] = {"root": root, "title": title, "description": description or ""}
    chosen = _optional_text(notebook_id, "notebook id", "notebooks.create")
    if chosen is not None:
        params["notebook_id"] = chosen
    return _object(_call("notebooks.create", params), "notebook")


def set_notebook_metadata(
    root: str,
    notebook_id: str,
    title: str,
    description: str = "",
) -> dict[str, Any]:
    """Rename a notebook and say what it is for."""
    _require_text(root, "root", "notebooks.set_metadata")
    _require_text(notebook_id, "notebook id", "notebooks.set_metadata")
    _require_text(title, "title", "notebooks.set_metadata")
    if description is not None and not isinstance(description, str):
        raise NotebookError(
            "the description of a notebook request must be text.",
            reason="the description is not text",
            details={"method": "notebooks.set_metadata"},
        )
    return _object(
        _call(
            "notebooks.set_metadata",
            {
                "root": root,
                "notebook_id": notebook_id,
                "title": title,
                "description": description or "",
            },
        ),
        "notebook",
    )


def add_source(root: str, notebook_id: str, source_id: str) -> dict[str, Any]:
    """Hold a source in a notebook. The source itself is not touched."""
    _require_text(root, "root", "notebooks.add_source")
    _require_text(notebook_id, "notebook id", "notebooks.add_source")
    _require_text(source_id, "source id", "notebooks.add_source")
    return _object(
        _call(
            "notebooks.add_source",
            {"root": root, "notebook_id": notebook_id, "source_id": source_id},
        ),
        "notebook",
    )


def remove_source(root: str, notebook_id: str, source_id: str) -> dict[str, Any]:
    """Stop holding a source. The source stays in the library."""
    _require_text(root, "root", "notebooks.remove_source")
    _require_text(notebook_id, "notebook id", "notebooks.remove_source")
    _require_text(source_id, "source id", "notebooks.remove_source")
    return _object(
        _call(
            "notebooks.remove_source",
            {"root": root, "notebook_id": notebook_id, "source_id": source_id},
        ),
        "notebook",
    )


def add_note(root: str, notebook_id: str, note_id: str) -> dict[str, Any]:
    """Refer to a note from a notebook.

    A note id is a relative path with ``/`` separators, so it is a path and
    not a name: what makes it safe to write is the tier's rule.
    """
    _require_text(root, "root", "notebooks.add_note")
    _require_text(notebook_id, "notebook id", "notebooks.add_note")
    _require_text(note_id, "note id", "notebooks.add_note")
    return _object(
        _call(
            "notebooks.add_note",
            {"root": root, "notebook_id": notebook_id, "note_id": note_id},
        ),
        "notebook",
    )


def remove_note(root: str, notebook_id: str, note_id: str) -> dict[str, Any]:
    """Stop referring to a note."""
    _require_text(root, "root", "notebooks.remove_note")
    _require_text(notebook_id, "notebook id", "notebooks.remove_note")
    _require_text(note_id, "note id", "notebooks.remove_note")
    return _object(
        _call(
            "notebooks.remove_note",
            {"root": root, "notebook_id": notebook_id, "note_id": note_id},
        ),
        "notebook",
    )


def delete_notebook(root: str, notebook_id: str) -> bool:
    """Delete a notebook. The sources and notes it pointed at are not."""
    _require_text(root, "root", "notebooks.delete")
    _require_text(notebook_id, "notebook id", "notebooks.delete")
    result = _call("notebooks.delete", {"root": root, "notebook_id": notebook_id})
    return bool(result.get("deleted", False))


__all__ = [
    "add_note",
    "add_source",
    "create_notebook",
    "delete_notebook",
    "list_notebooks",
    "read_notebook",
    "remove_note",
    "remove_source",
    "set_notebook_metadata",
]
