"""The API's bearer credential: where it comes from, where it is kept, how it is checked.

The API is a local service, and "local" is not a boundary. Any process on the
machine can reach the loopback interface, and a caller that is not a browser
simply omits ``Origin`` -- so the origin rules in :mod:`lmeditor.server.app`
say what a *page* may do and say nothing about what a *process* may do.

This module is what answers the second question. Every route under ``/api/``
requires a bearer token, and a request that does not carry it is refused
before it reaches a route. The credential is the same for every caller, so it
identifies the *operator* rather than a person: there is one operator and one
token, and the token says nothing about who presented it.

The contract this module implements is ``docs/api-authentication-blueprint.md``.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Mapping

from fastapi import FastAPI, Request, Response, status
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

#: Environment variable naming the operator's own token.
#:
#: The token is used verbatim when it is set, and it is the operator's to
#: choose: a token that came from somewhere else -- a password manager, a
#: secret store -- is better than one this program made up.
API_TOKEN_ENV = "LMEDITOR_API_TOKEN"

#: Environment variable naming the file the token is kept in.
API_TOKEN_FILE_ENV = "LMEDITOR_API_TOKEN_FILE"

#: The number of random bytes a generated token is made of.
#:
#: Thirty-two bytes is the width of a UUID and of an HMAC key, and it is
#: enough that the token cannot be guessed by trying: there are more of them
#: than there are particles in the observable universe.
TOKEN_BYTES = 32

#: The realm the refusal names.
#:
#: A realm is the "protection space" the credential belongs to. There is one
#: here -- this API -- and the refusal names it so a client knows which
#: credential to present.
REALM = "lmeditor"

#: What a refusal says.
#:
#: The same words for a missing credential and for a wrong one. A caller that
#: could tell the two apart would learn whether the token it guessed was the
#: right length, and a refusal that differs between the two tells a caller
#: what to fix.
REFUSAL_DETAIL = "This API requires a bearer token."

#: Where the credential is kept on the application.
_CREDENTIAL_STATE = "lmeditor_credential"

#: The prefix every route under the API's own namespace shares.
_API_PREFIX = "/api"


class CredentialUnavailableError(RuntimeError):
    """The credential could not be established, so the server cannot serve.

    This is a startup failure and not a request-time one: it is raised while
    the server is settling what it is served with, and it stops the server
    from starting. It is deliberately not a
    :class:`~lmeditor.core.exceptions.LMeditorError`, because those are
    request-time domain failures that are answered with a status code, and
    this one is answered by not answering at all.
    """


@dataclass(frozen=True)
class Credential:
    """The token the API is served with, and where it came from."""

    #: The token itself. It is never logged and never leaves the server.
    token: str

    #: The file the token is kept in. The path is not a secret.
    path: Path

    #: Where the token came from: ``environment``, ``file`` or ``generated``.
    source: str

    @property
    def generated(self) -> bool:
        """Whether this token had to be made up, because nothing else supplied one."""
        return self.source == SOURCE_GENERATED


#: The token was named by the operator in the environment.
SOURCE_ENVIRONMENT = "environment"

#: The token was read from the token file.
SOURCE_FILE = "file"

#: The token was generated, because nothing else supplied one.
SOURCE_GENERATED = "generated"


def token_file_path(environment: Mapping[str, str] | None = None) -> Path:
    """Where the token is kept.

    The operator may name the file; otherwise it is in the operator's home
    directory, beside the other operator-level state (``LMEDITOR_VAULT_BASE``
    and ``LMEDITOR_NOTEBOOKS_BASE`` both default to the home directory too).
    The token belongs with them.
    """
    env = os.environ if environment is None else environment
    named = env.get(API_TOKEN_FILE_ENV, "").strip()
    if named:
        return Path(named).expanduser()
    return Path.home() / ".lmeditor" / "api-token"


def resolve_credential(environment: Mapping[str, str] | None = None) -> Credential:
    """The token the API is served with.

    It is decided once, in this order: the operator's own token in
    ``LMEDITOR_API_TOKEN``, the token in the token file, and then a newly
    generated one. Whatever it turns out to be, it is written to the token
    file -- the server and the desktop client are separate processes, and the
    file is how one hands the credential to the other.

    The write is idempotent: a file that already holds the effective token is
    not rewritten, so the file's modification time says when the token last
    changed and nothing else.

    Raises:
        CredentialUnavailableError: The token had to be generated and the
            token file could not be written. A generated token that cannot be
            handed to the client is a server the client cannot talk to, and a
            server that refuses to start says so -- a server that starts and
            answers 401 for ever does not.
    """
    env = os.environ if environment is None else environment
    path = token_file_path(env)
    named = env.get(API_TOKEN_ENV, "").strip()
    stored = _read_token_file(path)

    if named:
        token, source = named, SOURCE_ENVIRONMENT
    elif stored:
        token, source = stored, SOURCE_FILE
    else:
        token, source = _generate_token(), SOURCE_GENERATED

    if stored != token:
        try:
            _write_token_file(path, token)
        except OSError as exc:
            if source == SOURCE_GENERATED:
                raise CredentialUnavailableError(
                    f"the API token could not be written to {path}: {exc}"
                ) from exc
            # The operator named the token and may well be running the client
            # with the same environment variable, so the server can serve
            # without the file. The path is logged; the token never is.
            logger.warning(
                "The API token could not be written to %s: %s", path, exc
            )

    return Credential(token=token, path=path, source=source)


def credential_for(app: FastAPI) -> Credential:
    """The credential this application is served with, resolved once.

    The credential is resolved when the application starts and kept on the
    application's state, so that every request is checked against the same
    token. It is resolved lazily as well as eagerly: the application object
    exists before the server has started -- importing the module builds it --
    and a credential settled at import time would be settled before the
    operator's environment was in place.
    """
    cached = getattr(app.state, _CREDENTIAL_STATE, None)
    if isinstance(cached, Credential):
        return cached
    resolved = resolve_credential()
    setattr(app.state, _CREDENTIAL_STATE, resolved)
    logger.info("The API is served with a bearer token kept in %s", resolved.path)
    return resolved


def is_preflight(request: Request) -> bool:
    """Whether this is a browser asking what it may do before it does it."""
    return (
        request.method == "OPTIONS"
        and header(request, "access-control-request-method") is not None
    )


def authorization_header(request: Request) -> str | None:
    """The ``Authorization`` header, whatever case it was spelled in."""
    return header(request, "authorization")


def header(request: Request, name: str) -> str | None:
    """One header of the request, whatever case its name was spelled in.

    The scope carries the header names as the server spelled them, and the
    names of a header are case-insensitive. Looking one up by comparing the
    names as they come would therefore hide a header that is really there.

    The rule matters in both directions. A header the server cannot find is
    a header the server does not act on: a credential it cannot read is a
    request it refuses, and an ``Origin`` it cannot read is a mutation it
    lets through that it should have refused.
    """
    raw: list[tuple[bytes, bytes]] = request.scope.get("headers", [])
    wanted = name.lower()
    for key, value in raw:
        if key.decode("latin-1").lower() == wanted:
            return value.decode("latin-1")
    return None


def presented_token(header: str | None) -> str | None:
    """The token a request carries, or ``None`` when it carries none.

    The scheme is matched without regard to case, because an HTTP
    authentication scheme is case-insensitive and a client that spells it
    ``bearer`` has still presented a bearer token. The token itself is
    matched exactly: it is opaque, and a client that has it right has it
    right.
    """
    if header is None:
        return None
    parts = header.split(None, 1)
    if len(parts) != 2:
        return None
    scheme, value = parts
    if scheme.lower() != "bearer":
        return None
    token = value.strip()
    return token or None


def token_matches(presented: str | None, expected: str) -> bool:
    """Whether the presented token is the expected one.

    The comparison is constant-time, so a caller that guesses tokens one at
    a time cannot time its way to the right one. The two tokens are hashed
    before they are compared, because the comparison of two byte strings is
    constant-time only when they are the same length, and the length of the
    expected token should not be something a caller can measure either.
    """
    if presented is None:
        return False
    return hmac.compare_digest(
        hashlib.sha256(presented.encode("utf-8")).digest(),
        hashlib.sha256(expected.encode("utf-8")).digest(),
    )


def refusal() -> Response:
    """The answer to a request that does not carry the credential.

    It is the same answer for a missing credential and for a wrong one: the
    same status, the same headers and the same body. It says nothing about
    the token file, about whether a token was presented, and about what was
    presented.
    """
    return JSONResponse(
        status_code=status.HTTP_401_UNAUTHORIZED,
        content={"detail": REFUSAL_DETAIL},
        headers={"WWW-Authenticate": f'Bearer realm="{REALM}"'},
    )


async def require_the_credential(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Refuse a request that does not carry the API's credential.

    This is the outermost thing the server does, so a request is
    authenticated before it is authorized: a caller that cannot prove it is
    the operator is not told whether the thing it asked for exists.

    Two requests are let through without a credential. The API's own
    documentation (``/docs``, ``/redoc``, ``/openapi.json``) is not under
    ``/api/`` and describes the API without acting on anything, and the
    Swagger UI fetches ``openapi.json`` without an ``Authorization`` header,
    so credentialing it would break the page rather than protect anything.
    And a browser's preflight is let through, because a browser does not
    attach the ``Authorization`` header to a preflight -- it attaches it to
    the request the preflight is about -- and a preflight that was refused
    would stop the browser from ever making the request it was asking about.
    """
    if not _is_credentialed_path(request.url.path):
        return await call_next(request)
    if is_preflight(request):
        return await call_next(request)
    expected = credential_for(request.app).token
    presented = presented_token(authorization_header(request))
    if not token_matches(presented, expected):
        return refusal()
    return await call_next(request)


def _is_credentialed_path(path: str) -> bool:
    """Whether this path is under the API's own namespace.

    The namespace is the prefix and not the exact route, so a route added
    later under it is credentialed without this module having to know about
    it. The API's own documentation is outside it, by construction.
    """
    return path == _API_PREFIX or path.startswith(_API_PREFIX + "/")


def _generate_token() -> str:
    """A new token, made of random bytes and said in URL-safe base64.

    It carries no meaning: no operator name, no timestamp, no counter, so
    it says nothing to a caller that reads it.
    """
    return secrets.token_urlsafe(TOKEN_BYTES)


def _read_token_file(path: Path) -> str | None:
    """The token the file holds, or ``None`` when it holds none.

    A file that cannot be read holds no token as far as this function is
    concerned: the server cannot use a token it cannot see. The file is
    then rewritten with the effective token, which is how a file left behind
    by an older run is taken over.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None
    token = raw.strip()
    return token or None


def _write_token_file(path: Path, token: str) -> None:
    """Write the token to the file, readable by the operator alone.

    The directory is created with mode ``0700`` and the file with mode
    ``0600`` on every platform that has modes; on Windows the file inherits
    the operator's own profile ACL, which is that platform's answer to the
    same question. The write goes to a temporary file and is then moved into
    place, so a reader never sees a half-written token.
    """
    directory = path.parent
    _ensure_private_directory(directory)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=directory,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    )
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(token + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _ensure_private_directory(directory: Path) -> None:
    """Create the directory, and any ancestor of it that is missing, privately.

    Every directory made here is made with mode ``0700``, including the
    ones above it: a directory that holds the token must not be one that
    another account can walk into.
    """
    missing: list[Path] = []
    current = directory
    while not current.exists():
        missing.append(current)
        parent = current.parent
        if parent == current:
            break
        current = parent
    for target in reversed(missing):
        target.mkdir(mode=0o700)
    if missing:
        os.chmod(directory, 0o700)
