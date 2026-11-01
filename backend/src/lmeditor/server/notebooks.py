"""The notebooks folder the server holds.

A notebook is a folder on the operator's disk, so the server holds a folder
of notebooks rather than a notebook: the folder the operator chose with
``POST /api/v1/notebooks/open``, and until then the default one --
``<user home>/.lmeditor/notebooks``, or whatever ``LMEDITOR_NOTEBOOKS_DIR``
points at.

The folder is process-wide state, and it is guarded: the swap is made under a
lock, so a request that opens a folder and a request that reads the one that
is open cannot tear the value between them.

The shape of this module follows the active vault
(:mod:`lmeditor.server.vault`): the root is checked before it is opened, a
refused root leaves the folder that was open exactly where it was, and the
root travels with every call, because the tier keeps no state of its own.

The root is also confined, because a request that arrives over the network
can name it: it has to be a local folder inside the base the operator
configured for the notebooks (see :mod:`lmeditor.server.roots`), and a
network share is refused outright. The same check runs again when the
folder is used, so a read, a write and a delete are all confined by it.
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

from lmeditor.core.exceptions import NotebookError
from lmeditor.server.roots import (
    NOTEBOOKS_BASE_ENV,
    is_network_path,
    notebooks_base,
    refusal_reason,
)
from lmeditor.server.routes_helpers import safe_message

logger = logging.getLogger(__name__)

#: Environment variable that redirects the notebooks folder.
NOTEBOOKS_DIR_ENV = "LMEDITOR_NOTEBOOKS_DIR"

#: The default notebooks folder: the operator's own, under their home
#: directory.
DEFAULT_NOTEBOOKS_DIR = Path.home() / ".lmeditor" / "notebooks"

#: The folder the operator chose, or ``None`` when they have not chosen one.
_ACTIVE_ROOT: str | None = None

#: The lock that guards the folder the server holds.
_lock = threading.Lock()


def default_notebooks_root() -> Path:
    """The folder the notebooks live in when the operator has not chosen one.

    ``LMEDITOR_NOTEBOOKS_DIR`` overrides it, the way it overrides the library
    root, so a test can redirect the folder without writing into the
    operator's home directory.
    """
    override = os.environ.get(NOTEBOOKS_DIR_ENV)
    if override:
        return Path(override)
    return DEFAULT_NOTEBOOKS_DIR


def notebooks_root() -> str:
    """The folder the server holds the notebooks in.

    It is the folder the operator chose, and until they choose one it is the
    default folder.
    """
    with _lock:
        active = _ACTIVE_ROOT
    if active is not None:
        return active
    return str(default_notebooks_root())


def active_notebooks_root() -> str | None:
    """The folder the operator chose, or ``None`` when they have not."""
    with _lock:
        return _ACTIVE_ROOT


def require_notebooks_root() -> str:
    """The root of the notebooks folder, ready to be asked about.

    The folder always exists: the one the operator chose was already there,
    and the default one is made when it is first needed. There is no answer
    of "there is no folder yet" to give, because there is no state in which
    the notebooks folder is missing.

    The root is checked again here, not only when it was opened, so that
    every read, every write and every delete is confined by the same rule
    that opened the folder. The check runs before the default folder is
    made, so a folder outside the base is never created either.
    """
    resolved = _checked_root(notebooks_root(), must_exist=False)
    _ensure_folder(resolved)
    return resolved


def open_notebooks_root(root: str) -> str:
    """Open a notebooks folder and make it the one the server holds.

    The root has to be an absolute path to a local folder that is there and
    that lives inside the base the operator configured for the notebooks.
    The folder is opened only when it is usable, so a refused root leaves
    the folder that was open before it exactly where it was.

    Args:
        root: The absolute path of the folder of notebooks.

    Returns:
        The root as an absolute path, resolved.

    Raises:
        NotebookError: The root is empty, is not absolute, is a network path,
            is not there, is not a folder or is outside the permitted base.
    """
    global _ACTIVE_ROOT
    resolved = _checked_root(root)
    with _lock:
        _ACTIVE_ROOT = resolved
    return resolved


def close_notebooks_root() -> None:
    """Forget the folder the operator chose."""
    global _ACTIVE_ROOT
    with _lock:
        _ACTIVE_ROOT = None


def _checked_root(root: str, *, must_exist: bool = True) -> str:
    """The root as an absolute, confined path, or a refusal that says what was wrong."""
    text = (root or "").strip()
    if not text:
        raise NotebookError("The notebooks root is empty.", reason="the root is empty")
    if is_network_path(text):
        # Refused on the text, before the filesystem is touched: a share is
        # never contacted and no credential is ever offered for it.
        raise NotebookError(
            f"The notebooks root '{text}' is a network or a device path, and is refused.",
            reason="the root is a network or a device path",
            details={"root": text},
        )
    path = Path(text)
    if not path.is_absolute():
        raise NotebookError(
            f"The notebooks root '{text}' is not an absolute path.",
            reason="the root is not an absolute path",
            details={"root": text},
        )
    if must_exist:
        if not path.exists():
            raise NotebookError(
                f"The notebooks root '{text}' does not exist.",
                reason="the root does not exist",
                details={"root": text},
            )
        if not path.is_dir():
            raise NotebookError(
                f"The notebooks root '{text}' is not a directory.",
                reason="the root is not a directory",
                details={"root": text},
            )
    resolved = path.resolve()
    reason = refusal_reason(resolved, notebooks_base())
    if reason is not None:
        raise NotebookError(
            f"The notebooks root '{text}' is refused: {reason}. "
            f"Set {NOTEBOOKS_BASE_ENV} to the folder that holds it.",
            reason=reason,
            details={"root": text},
        )
    return str(resolved)


def _ensure_folder(root: str) -> None:
    """Make sure the folder the notebooks live in is there.

    The default folder is the operator's own and is made when it is first
    needed; a folder the operator chose was already there, and is left
    exactly as it is.
    """
    path = Path(root)
    if path.is_dir():
        return
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        # The folder that could not be made is the server's own, and so is
        # the reason: the client is told which folder failed and nothing
        # about the disk, and the whole of it goes to the log.
        logger.error(
            "The notebooks root could not be created: %s: %s", root, exc, exc_info=exc
        )
        raise NotebookError(
            safe_message(f"The notebooks root '{root}' could not be created: {exc}"),
            reason="the root could not be created",
            details={"root": root},
        ) from exc


__all__ = [
    "DEFAULT_NOTEBOOKS_DIR",
    "NOTEBOOKS_DIR_ENV",
    "active_notebooks_root",
    "close_notebooks_root",
    "default_notebooks_root",
    "notebooks_root",
    "open_notebooks_root",
    "require_notebooks_root",
]
