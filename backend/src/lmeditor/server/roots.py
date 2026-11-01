"""The confinement policy for a folder the operator names.

The server lets the operator point it at a folder on their own disk: the
vault, or the folder of notebooks. That is a real product feature, and it is
also the one place where a request that arrives over the network can name a
path on the machine. The policy here is what keeps the second reading from
winning:

* a network or a device path (``\\\\server\\share``, ``\\\\?\\``, ``\\\\.\\``) is
  refused before the filesystem is touched, so no share is contacted and no
  credential is ever offered;
* a root that resolves inside a protected system location -- the Windows
  system directory, the Program Files directory, the shared program data --
  is refused;
* a root that is the root of a filesystem, or the folder that holds every
  user profile, is refused; that folder is judged on the resolved path as
  well, so a home directory reached through a symbolic link cannot hide
  it;
* a root has to sit inside the base the operator configured for it, and the
  base is checked on the RESOLVED path, so a symlink or a junction cannot
  walk out of it.

The base is the operator's own home directory unless it is configured:
``LMEDITOR_VAULT_BASE`` for the vault and ``LMEDITOR_NOTEBOOKS_BASE`` for the
notebooks. The operator widens it when their work lives somewhere else.

The policy is a pure function of a root and a base: it answers with the
reason a root is refused, and each domain -- the vault, the notebooks --
turns that reason into its own typed error.
"""

from __future__ import annotations

import os
from pathlib import Path

#: Environment variable naming the base a vault root may live in.
VAULT_BASE_ENV = "LMEDITOR_VAULT_BASE"

#: Environment variable naming the base a notebooks root may live in.
NOTEBOOKS_BASE_ENV = "LMEDITOR_NOTEBOOKS_BASE"

#: The Windows folders no root may live in.
_PROTECTED_WINDOWS: tuple[str, ...] = (
    "SystemRoot",
    "ProgramFiles",
    "ProgramFiles(x86)",
    "ProgramData",
)

#: The POSIX folders no root may live in.
_PROTECTED_POSIX: tuple[str, ...] = (
    "/bin",
    "/boot",
    "/dev",
    "/etc",
    "/lib",
    "/lib64",
    "/proc",
    "/sbin",
    "/sys",
    "/usr",
)


def vault_base() -> Path:
    """The folder a vault root has to live in.

    ``LMEDITOR_VAULT_BASE`` names it; the operator's home directory is the
    default, because the operator's own tree is the one place their work is.
    """
    return _base(VAULT_BASE_ENV)


def notebooks_base() -> Path:
    """The folder a notebooks root has to live in.

    ``LMEDITOR_NOTEBOOKS_BASE`` names it; the operator's home directory is
    the default, and the default notebooks folder lives inside it.
    """
    return _base(NOTEBOOKS_BASE_ENV)


def is_network_path(text: str) -> bool:
    """Whether this text names a network share or a device path.

    The answer is decided on the text, before the filesystem is touched: a
    path that names a share is refused without a single call to the network,
    so no credential is ever offered to a server the operator did not choose.
    """
    stripped = text.strip()
    return stripped.startswith(("\\\\", "//"))


def refusal_reason(root: Path, base: Path) -> str | None:
    """Why this root may not be used, or ``None`` when it may.

    ``root`` is the RESOLVED root and ``base`` is the permitted base. The
    reason names what is wrong without naming a path, because it travels
    back to the caller.
    """
    if _is_filesystem_root(root):
        return "it is the root of a filesystem"
    if _holds_user_profiles(root):
        return "it is the folder that holds the user profiles"
    if _in_protected_location(root):
        return "it is inside a protected system location"
    if not _inside(root, base):
        return "it is outside the permitted base"
    return None


def _base(env_var: str) -> Path:
    """The base named by the environment, or the operator's home directory."""
    configured = os.environ.get(env_var, "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path.home()


def _is_filesystem_root(root: Path) -> bool:
    """Whether this path is the root of a filesystem (``C:\\`` or ``/``)."""
    return root.parent == root


def _holds_user_profiles(root: Path) -> bool:
    """Whether this path is the folder that holds every user profile.

    On Windows that is ``C:\\Users``; on POSIX it is the folder the home
    directory lives in. It holds every account on the machine, so it is
    never one operator's folder.

    Both sides are compared on the same footing. The root arrives
    resolved, so the folder the home lives in is resolved as well: a home
    directory that is reached through a symbolic link -- which is what a
    temporary folder is on a POSIX machine, where ``/var`` is a link to
    ``/private/var`` -- names the same folder either way, and the rule
    fires on every platform rather than only on the ones whose temporary
    folder happens to be spelled as it resolves.
    """
    home = Path.home()
    if home.parent == home:
        return False
    return root == _resolve(home.parent)


def _in_protected_location(root: Path) -> bool:
    """Whether this path is inside a protected system location."""
    return any(_inside(root, protected) for protected in _protected_roots())


def _protected_roots() -> list[Path]:
    """The protected locations of this machine."""
    if os.name == "nt":
        roots: list[Path] = []
        for env_var in _PROTECTED_WINDOWS:
            value = os.environ.get(env_var, "").strip()
            if value:
                roots.append(Path(value))
        return roots
    return [Path(candidate) for candidate in _PROTECTED_POSIX]


def _inside(root: Path, base: Path) -> bool:
    """Whether ``root`` is ``base`` or lies inside it.

    The comparison is made on resolved paths and through the parents of the
    path, never on the text of the path: a prefix of a string is not a
    folder, and ``C:\\Users\\bob`` is not inside ``C:\\Users``.
    """
    resolved_base = _resolve(base)
    if root == resolved_base:
        return True
    return resolved_base in root.parents


def _resolve(path: Path) -> Path:
    """The path with its symbolic links and junctions resolved."""
    try:
        return path.resolve()
    except OSError:
        return path.absolute()


__all__ = [
    "NOTEBOOKS_BASE_ENV",
    "VAULT_BASE_ENV",
    "is_network_path",
    "notebooks_base",
    "refusal_reason",
    "vault_base",
]
