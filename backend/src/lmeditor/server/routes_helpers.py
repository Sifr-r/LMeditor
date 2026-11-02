"""Helpers for the server's routes: uploaded sources, and what the server says."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path

from fastapi import UploadFile

from lmeditor.bridge.errors import BridgeProtocolError, BridgeUnavailableError
from lmeditor.core.exceptions import SourceUploadTooLargeError, ValidationError

#: Fallback ceiling for an uploaded artifact, used when the ingestion package
#: does not publish one. 50 MiB is comfortably above a research paper or a
#: report and far below anything that would exhaust the process's memory.
DEFAULT_MAX_UPLOAD_BYTES: int = 50 * 1024 * 1024

#: How many bytes are read from the upload at a time.
_UPLOAD_CHUNK_BYTES: int = 1024 * 1024

#: The tier's own rendering of its details, appended to every message it
#: raises: ``... | Details: {key=value}``. It is the JVM's ``Map.toString``,
#: and it carries paths and internals that are the server's, not the
#: caller's.
#:
#: The values of that map may themselves be maps, so the rendering nests one
#: level of braces and the whole of it is taken out: a rendering taken out
#: only to its innermost brace would leave the keys of the outer map behind,
#: and a key names the server's own thing as much as a value does.
#:
#: A rendering that is never closed is not a rendering: there is no fragment
#: to take out, and taking the rest of the message with it would take the
#: failure away with it. Its marker is taken out all the same -- a marker
#: that says a fragment was removed, when nothing was, points at nothing.
_DETAILS_FRAGMENT = re.compile(
    r"\s*\|\s*Details:\s*\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}"
)

#: The marker of a rendering that is never closed.
#:
#: The marker is the ``| Details:`` and the brace that opens the rendering.
#: Both are taken out, so that no marker is left behind pointing at a
#: fragment that was never there; what follows them is the failure's own
#: text, and it is kept, because taking it with the marker would take the
#: failure away with it.
#:
#: A rendering is unclosed when no closing brace follows it on the line. A
#: rendering that *is* closed has already been taken out whole, marker and
#: all, by :data:`_DETAILS_FRAGMENT` before this rule is reached.
#:
#: The whitespace before the marker is left where it is, so that the words
#: on either side of it are not glued together.
_DETAILS_UNCLOSED = re.compile(r"\|\s*Details:\s*\{?(?![^\n]*\})")

#: A Windows absolute path: a drive letter and everything that follows it.
#: The letter does not continue a word, so the scheme of a URL
#: (``https://host/path``) is not read as a drive.
_WINDOWS_PATH = re.compile(r"(?<![A-Za-z0-9_])[A-Za-z]:[\\/][^\n\"',;|}\])]*")

#: A UNC path: a host and the share on it.
_UNC_PATH = re.compile(r"\\\\[^\s\"',;|}\])]+")

#: HTTP(S) URLs, followed by POSIX absolute paths of any depth. Match a URL
#: whole before looking for paths so IPv6 authorities and doubled slashes
#: in its path survive. The replacement keeps that named group unchanged.
#: A separate path following the URL is still matched and redacted.
#:
#: A root such as ``/workspace`` is as private as a nested path; no known-root
#: list can cover operator-configured storage. Word-internal separators such
#: as ``read/write`` are excluded by the lookbehind. The double-slash rule
#: retains the existing treatment of filesystem paths and file: URLs.
_POSIX_PATH = re.compile(
    r"(?P<url>\bhttps?://[^\s\"',;|})]+)"
    r"|(?<![A-Za-z0-9_/])/[^\s/\"',;|}\])]+(?:/[^\s\"',;|}\])]+)*/?"
    r"|(?<![A-Za-z0-9_:])//[^\s/\"',;|}\])]+(?:/[^\s\"',;|}\])]+)*/?",
    re.IGNORECASE,
)

#: A JVM stack frame: ``at lmeditor.bridge.BridgeMain.main(BridgeMain.kt:42)``.
_JVM_FRAME = re.compile(r"\bat\s+[A-Za-z0-9_.$]+\([^()]*\)")

#: A JVM or Kotlin class name: ``java.io.IOException``,
#: ``lmeditor.bridge.BridgeMain``. The first part of the name is a package
#: and the last is a class, so the first is lower case and the last is
#: upper case -- which is also what tells a class apart from the host of a
#: URL, which is lower case throughout.
_JVM_CLASS = re.compile(r"\b(?:[a-z][A-Za-z0-9_]*\.)+[A-Z][A-Za-z0-9_]*\b")

#: A bare exception name: ``ClassNotFoundException``, ``IOException``.
_BARE_EXCEPTION = re.compile(r"\b[A-Z][A-Za-z0-9_]*(?:Exception|Error)\b")

#: An address the server resolved a host to, or connected to. The sense of
#: a refusal is in the sentence around the address; the address itself is
#: the server's own.
_IP_ADDRESS = re.compile(
    r"(?<![0-9A-Za-z.])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![0-9A-Za-z.])"
    r"|(?<![0-9A-Za-z:.])::(?:[0-9A-Fa-f]{1,4}:){0,6}[0-9A-Fa-f]{1,4}(?![0-9A-Za-z:])"
    r"|(?<![0-9A-Za-z:.])(?:[0-9A-Fa-f]{1,4}:){7}[0-9A-Fa-f]{1,4}(?![0-9A-Za-z:])"
    r"|(?<![0-9A-Za-z:.])(?:[0-9A-Fa-f]{1,4}:){1,6}:[0-9A-Fa-f]{1,4}(?![0-9A-Za-z:])"
    r"|(?<![0-9A-Za-z:.])(?:[0-9A-Fa-f]{1,4}:){1,7}:(?![0-9A-Za-z:])"
)

#: What is said when there is nothing left to say.
_EMPTY_MESSAGE = "The request could not be completed."

#: What is said when a destination was refused and no more is known about
#: why. The reason itself goes to the log.
_ORIGIN_REFUSED = "the destination is not permitted by this server's policy."

#: The base of what is said when the Kotlin tier could not be reached at all.
#:
#: Why the tier is not there is the server's own business: the reason names
#: the JVM, the class it was looking for and the jars it looked through, and
#: none of that may leave the process. A sanitised version of it would still
#: describe the machine, so the client is told that the tier is not
#: available and the reason itself goes to the log.
_TIER_UNAVAILABLE = "The Kotlin tier is not available"

#: What is said when the Kotlin tier could not be reached at all.
TIER_UNAVAILABLE_MESSAGE: str = f"{_TIER_UNAVAILABLE}."

#: What is said when the Kotlin tier answered with something unreadable.
TIER_ANSWER_UNREADABLE_MESSAGE: str = "The Kotlin tier's answer could not be read."


def _without_machine_detail(text: str) -> str:
    """This text with the machine's own detail taken out of it.

    What is taken out is the shape of the machine behind a failure: the
    absolute paths, the tier's own rendering of a map of details, and the
    JVM's own internals -- the class it died with and the frames it died
    in. What is kept is the failure itself: what went wrong, in the
    server's own words.

    Nothing else is touched. The whitespace, the wording and the shape of
    the rest of the text are left exactly as they were, so a value that
    carries no machine detail comes back byte for byte.

    Args:
        text: The text a failure carries.

    Returns:
        The text with the machine's detail taken out of it.
    """
    previous = ""
    while previous != text:
        previous = text
        text = _DETAILS_FRAGMENT.sub("", text)
    text = _DETAILS_UNCLOSED.sub("", text)
    text = _JVM_FRAME.sub("<frame>", text)
    text = _WINDOWS_PATH.sub("<path>", text)
    text = _UNC_PATH.sub("<path>", text)
    text = _POSIX_PATH.sub(lambda match: match.group("url") or "<path>", text)
    text = _JVM_CLASS.sub("<class>", text)
    text = _BARE_EXCEPTION.sub("<class>", text)
    return text


def _tidied(text: str) -> str:
    """This text with the gaps the taking out of a detail leaves closed.

    Taking a ``| Details:`` rendering out of a message leaves a space and a
    pipe behind it, and taking a path out of one leaves two spaces. A
    message is read by a person, so the leftovers are closed up: the
    whitespace is collapsed, and a pipe that is left with nothing around it
    is taken away.

    Args:
        text: The text a failure carries.

    Returns:
        The text, tidied.
    """
    return re.sub(r"\s+", " ", text).strip().strip("|").strip()


def safe_message(message: str) -> str:
    """The message as it may leave the server.

    A message that describes a failure is worth keeping: it says what went
    wrong. What is not worth keeping is the shape of the machine behind it
    -- an absolute path, the JVM's own rendering of a map of details, the
    class the JVM died with -- so those are taken out. The whole of it
    stays in the server's log.

    Args:
        message: The message a domain error carries.

    Returns:
        The message with the paths, the raw tier details and the JVM's own
        internals taken out.
    """
    return _tidied(_without_machine_detail(message or "")) or _EMPTY_MESSAGE


def safe_detail_value(value: str) -> str:
    """One value of the details of a failure, as it may leave the server.

    The machine's own detail is taken out of it -- the absolute paths, the
    tier's own rendering of a map of details, the JVM's class names and
    frames -- and nothing else is touched: the value keeps its whitespace
    and its wording, so a client that keys on a detail still finds the
    detail it sent, and a detail that names no folder comes back byte for
    byte.

    Args:
        value: One value of the details of a failure.

    Returns:
        The value with the machine's detail taken out of it.
    """
    return _without_machine_detail(value)


def safe_origin_reason(reason: str) -> str:
    """The reason a destination was refused, as it may leave the server.

    The reason says why a destination was refused, and that is the
    caller's business: it is what the caller needs in order to fix its
    request. The addresses in it are the server's own -- the address a
    host resolved to and the address it actually connected to -- so they
    are taken out. The host stays: it is the caller's own, and it is the
    one thing the caller has to change.

    Args:
        reason: The reason the destination was refused.

    Returns:
        The reason with the addresses taken out.
    """
    text = _IP_ADDRESS.sub("<address>", _tidied(_without_machine_detail(reason or "")))
    return text or _ORIGIN_REFUSED


def safe_detail(exc: BaseException, prefix: str = "") -> str:
    """The detail a failed operation may carry to the client.

    The operation is kept -- a caller can still tell *what* did not happen --
    and the shape of the machine behind it is taken out: the absolute paths
    and the tier's own rendering of a map of details. The whole of it stays
    in the server's log.

    A failure of the seam is the one failure whose own words describe the
    machine in a way no sanitiser can find: the JVM's class names and its
    stderr carry no path to take out of them. Such a failure gets a generic
    message instead, and its reason goes to the log.

    Args:
        exc: The failure.
        prefix: What the server was doing, in the client's words.

    Returns:
        The detail, with the paths and the raw tier details taken out.
    """
    if isinstance(exc, BridgeUnavailableError):
        text = TIER_UNAVAILABLE_MESSAGE
    elif isinstance(exc, BridgeProtocolError):
        text = TIER_ANSWER_UNREADABLE_MESSAGE
    else:
        text = safe_message(str(exc))
    if not prefix:
        return text
    return f"{prefix}: {text}"


def tier_unavailable_detail(operation: str = "") -> str:
    """The detail a failure of the seam may carry to the client.

    The tier could not be reached, or could not serve the method at all.
    Why it could not is the server's own business -- the reason names the
    JVM, its class names and the stderr it died with -- so the client is
    told that the tier is not available and which operation it lost, and the
    reason goes to the log.

    Args:
        operation: What the server was doing, in the client's words.

    Returns:
        The generic message.
    """
    if not operation:
        return TIER_UNAVAILABLE_MESSAGE
    return f"{_TIER_UNAVAILABLE}, so {operation} could not be carried out."


@dataclass
class SavedFile:
    """A persisted uploaded file plus its on-disk path."""

    path: Path


def max_upload_bytes() -> int:
    """The ceiling for an uploaded artifact, in bytes."""
    try:
        from lmeditor.ingestion.limits import MAX_UPLOAD_BYTES

        return int(MAX_UPLOAD_BYTES)
    except Exception:  # noqa: BLE001 - the limit is optional
        return DEFAULT_MAX_UPLOAD_BYTES


async def persist_source_to_disk(file: UploadFile, source_id: str) -> SavedFile:
    """Save an uploaded file under a stable per-source directory.

    The destination is computed by the bounded
    :class:`~lmeditor.ingestion.source_store.SourceStore`, which proves the
    resolved path stays inside the upload root through
    :func:`lmeditor.ingestion.url_policy.safe_upload_path`. A traversal
    ``source_id`` (``../../evil``) or an absolute filename therefore raises
    :class:`~lmeditor.ingestion.url_policy.UnsafeSourceError` instead of
    writing outside the upload directory.

    The upload is read in bounded chunks and refused as soon as it exceeds
    :func:`max_upload_bytes`, so an oversized upload is never fully buffered.

    The store gives the upload directory as a whole a total-bytes budget, so
    it cannot grow by one file per upload: when the budget is exceeded, the
    files written longest ago are evicted.
    """
    limit = max_upload_bytes()
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(_UPLOAD_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise SourceUploadTooLargeError(
                f"Upload of '{source_id}' exceeds the {limit} byte limit.",
                limit_bytes=limit,
            )
        chunks.append(chunk)
    content = b"".join(chunks)

    try:
        path = await asyncio.to_thread(
            _store_save, source_id, file.filename or "upload", content
        )
    except ValidationError as exc:
        # The store re-checks the size against its own cap. Translate that
        # into the domain error the route turns into a 413.
        raise SourceUploadTooLargeError(
            f"Upload of '{source_id}' exceeds the {limit} byte limit.",
            limit_bytes=limit,
        ) from exc

    # Mark the file as in use. The store evicts the *oldest* file when the
    # upload directory is over its budget, and a concurrent upload could
    # otherwise evict this one while the ingestion pipeline is still reading
    # it. The touch moves this file's timestamp to the front of the queue.
    await asyncio.to_thread(_store_touch, source_id, file.filename or "upload")
    return SavedFile(path=path)


def _store_save(source_id: str, filename: str, content: bytes) -> Path:
    """Write an upload through the bounded store, off the event loop."""
    from lmeditor.ingestion.source_store import SourceStore

    # A fresh store per call, rather than the process-wide one: the upload
    # root is configurable per process (``LMEDITOR_UPLOAD_DIR``), and a cached
    # store would keep a root that is no longer the current one.
    return SourceStore.default().save(source_id, filename, content)


def _store_touch(source_id: str, filename: str) -> None:
    """Mark a stored upload as recently used, off the event loop."""
    from lmeditor.ingestion.source_store import SourceStore

    SourceStore.default().touch(source_id, filename)


