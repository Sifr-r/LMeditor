"""The active vault the server holds.

A vault is a folder the operator chose, and the server is the one that
holds it: the Kotlin tier keeps no state of its own, so every call carries
the root of the vault the operator is working in. One vault is active at a
time, because the window has one workspace.

The root is checked when it is taken rather than when it is used. A path
that is not absolute, that is not there or that is not a directory is
refused with a message that says which of those it was, so the operator is
told what to fix instead of being shown an empty vault.

The root is also confined, because a request that arrives over the network
can name it: it has to be a local folder inside the base the operator
configured for the vault (see :mod:`lmeditor.server.roots`), and a network
share is refused outright. The same check runs again when the vault is
used, so a read, a write and a delete are all confined by it.
"""

from __future__ import annotations

from pathlib import Path

from lmeditor.core.exceptions import NoVaultOpenError, VaultError
from lmeditor.server.roots import (
    VAULT_BASE_ENV,
    is_network_path,
    refusal_reason,
    vault_base,
)

#: The root of the open vault, when one is open.
_ACTIVE_ROOT: str | None = None


def active_vault_root() -> str | None:
    """The root of the open vault, or ``None`` when no vault is open."""
    return _ACTIVE_ROOT


def require_vault_root() -> str:
    """The root of the open vault, or a refusal when no vault is open.

    Every vault route asks for this first: the client has to open a vault
    before it can ask anything of one.

    The root is checked again here, not only when it was opened, so that
    every read, every write and every delete is confined by the same rule
    that opened the vault.
    """
    if _ACTIVE_ROOT is None:
        raise NoVaultOpenError(
            "No vault is open. Open one with POST /api/v1/vault/open "
            "before asking anything of it."
        )
    return _checked_root(_ACTIVE_ROOT)


def open_active_vault(root: str) -> str:
    """Open a vault folder and make it the active vault.

    The root has to be an absolute path to a local folder that is there and
    that lives inside the base the operator configured for the vault. The
    vault is opened only when it is usable, so a refused root leaves the
    vault that was open before it exactly where it was.
    """
    global _ACTIVE_ROOT
    resolved = _checked_root(root)
    _ACTIVE_ROOT = resolved
    return resolved


def close_active_vault() -> None:
    """Forget the open vault."""
    global _ACTIVE_ROOT
    _ACTIVE_ROOT = None


def _checked_root(root: str) -> str:
    """The root as an absolute, confined path, or a refusal that says what was wrong."""
    text = (root or "").strip()
    if not text:
        raise VaultError("The vault root is empty.", reason="the root is empty")
    if is_network_path(text):
        # Refused on the text, before the filesystem is touched: a share is
        # never contacted and no credential is ever offered for it.
        raise VaultError(
            f"The vault root '{text}' is a network or a device path, and is refused.",
            reason="the root is a network or a device path",
        )
    path = Path(text)
    if not path.is_absolute():
        raise VaultError(
            f"The vault root '{text}' is not an absolute path.",
            reason="the root is not an absolute path",
        )
    if not path.exists():
        raise VaultError(
            f"The vault root '{text}' does not exist.",
            reason="the root does not exist",
        )
    if not path.is_dir():
        raise VaultError(
            f"The vault root '{text}' is not a directory.",
            reason="the root is not a directory",
        )
    resolved = path.resolve()
    reason = refusal_reason(resolved, vault_base())
    if reason is not None:
        raise VaultError(
            f"The vault root '{text}' is refused: {reason}. "
            f"Set {VAULT_BASE_ENV} to the folder that holds it.",
            reason=reason,
        )
    return str(resolved)


__all__ = [
    "active_vault_root",
    "close_active_vault",
    "open_active_vault",
    "require_vault_root",
]
