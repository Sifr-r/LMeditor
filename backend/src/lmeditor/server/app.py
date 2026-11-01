"""FastAPI application configuration with CORS, lifespan handlers, and domain error mapping."""

import logging
import os
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Awaitable, Callable
from urllib.parse import urlsplit

from fastapi import FastAPI, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse

from lmeditor.core.exceptions import (
    BookNotFoundError,
    CitationHallucinationError,
    EditTargetNotFoundError,
    FactCheckError,
    InlineEditRejectedError,
    LMeditorError,
    LibraryError,
    LMNotConfiguredError,
    SectionNotFoundError,
    StateGraphDeadlockError,
    UngroundedClaimError,
    ValidationError,
    VerificationError,
    WebEvidenceError,
    WorkflowError,
)
from lmeditor.server.routes import router
from lmeditor.server.routes_helpers import safe_detail, safe_detail_value, safe_message

logger = logging.getLogger(__name__)

#: The origins a browser page may call this API from.
#:
#: There is no default list, and there cannot be one: the API's own origin
#: is the origin it is *served from*, and the port it is served on is not
#: a constant this code may know -- it is whatever the operator bound the
#: server to. A constant would have to name a port, and a page served on
#: that port of the loopback interface is a *different* server when the
#: API is served on another one, so a constant list would let that page
#: change things on the API. The API's own origin is therefore worked out
#: per request, from the request the browser actually made: see
#: :func:`_is_own_origin`.
#:
#: That rule is anchored on the loopback interface: the request must have
#: been addressed to a loopback name -- ``localhost``, ``127.0.0.1`` or
#: ``::1`` -- for its origin to count as the API's own. A request addressed
#: to any other name is not the API's own origin, however the page spells
#: its own origin: a page on a name that resolves to the loopback
#: interface would otherwise reach this API with its ``Host`` header and
#: its ``Origin`` in agreement and be answered as one of the API's own
#: pages. See :func:`_is_own_origin`.
#:
#: What the operator adds to ``LMEDITOR_ALLOWED_ORIGINS`` is the allowance
#: for a page that really is on another origin. An origin is a scheme, a
#: host and a port, and it is named in full: ``http://127.0.0.1:8000`` and
#: ``http://127.0.0.1:60804`` are two origins, and the operator's name
#: covers the one they wrote down and not the other. It is also how an API
#: that is *not* reached on the loopback interface is served from a page of
#: its own: an API on a LAN address has no own origin by the rule above,
#: and the operator names that origin here.
#:
#: A wildcard origin is never used. With credentials a wildcard is reflected
#: back as the caller's own origin, which lets any page on the network call
#: the API as the operator.
DEFAULT_ALLOWED_ORIGINS: tuple[str, ...] = ()

#: Environment variable naming the extra origins a browser page may use.
ALLOWED_ORIGINS_ENV = "LMEDITOR_ALLOWED_ORIGINS"

#: Environment variable that turns the API's own documentation off.
#:
#: The documentation is the shape of the whole API, so it is the operator's
#: call whether it is served. It is served by default, as it always was.
EXPOSE_DOCS_ENV = "LMEDITOR_EXPOSE_DOCS"

#: The methods that change something on the server.
_MUTATING_METHODS: frozenset[str] = frozenset({"POST", "PUT", "PATCH", "DELETE"})

#: The methods a browser page may use on this API. The list is explicit: a
#: page is told what it may do, and it is told nothing else.
_CORS_METHODS: tuple[str, ...] = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")

#: How long a browser may keep the answer to its own question about what it
#: may do.
_CORS_MAX_AGE: int = 600

#: The names the loopback interface answers to.
#:
#: These three are the whole list, because they are the names a browser
#: uses to reach the loopback interface: ``localhost``, ``127.0.0.1`` and
#: ``::1``. The rest of ``127.0.0.0/8`` is the same machine, but a browser
#: does not reach the API through it, and a server on one of those
#: addresses is another server.
_LOOPBACK_HOSTS: frozenset[str] = frozenset({"localhost", "127.0.0.1", "::1"})


def allowed_origins() -> list[str]:
    """The origins the operator has named for this API.

    The list holds only what the operator named. The API's own origin is
    not in it, because it is not a constant: it is the origin the request
    was addressed to, and it is worked out per request (see
    :func:`_is_own_origin`). Each name is an origin in full -- a scheme, a
    host and a port -- so an origin the operator names is allowed on that
    port and on no other.
    """
    origins = list(DEFAULT_ALLOWED_ORIGINS)
    configured = os.environ.get(ALLOWED_ORIGINS_ENV, "")
    for origin in configured.split(","):
        cleaned = origin.strip()
        if cleaned and cleaned not in origins:
            origins.append(cleaned)
    return origins


def expose_docs() -> bool:
    """Whether the API's own documentation is served.

    ``LMEDITOR_EXPOSE_DOCS=off`` turns it off, which takes ``/docs``,
    ``/redoc`` and ``/openapi.json`` away. The default is unchanged: the
    documentation is served.
    """
    setting = os.environ.get(EXPOSE_DOCS_ENV, "on").strip().lower()
    return setting not in {"off", "0", "false", "no"}


def _origin_allowed(origin: str, request: Request) -> bool:
    """Whether this origin may call this API.

    An origin is allowed when it is the origin the API is served from --
    a page the API itself serves is not a foreign page -- or when the
    operator has named it in ``LMEDITOR_ALLOWED_ORIGINS``.

    The first is the rule of :func:`_is_own_origin`, and it holds only for
    a request that was addressed to a loopback name. The second is an
    exact-origin allowance, and it holds wherever the API is served from.
    """
    if origin in allowed_origins():
        return True
    return _is_own_origin(origin, request)


def _is_own_origin(origin: str, request: Request) -> bool:
    """Whether this origin is the origin the API is served from.

    The origin of a request is the scheme, the host and the port it was
    addressed to. The API is served from the address the request was
    addressed to, so the question is whether the request was addressed to
    *this* server.

    The rule rests on one thing: the request must have been addressed to a
    loopback name -- ``localhost``, ``127.0.0.1`` or ``::1``. A browser
    reaches the loopback interface by one of those names and by no other,
    so a request that names one was addressed to the machine's own
    loopback interface and not to some other server that answers there.

    What the rule does *not* rest on is the ``Host`` header being beyond
    a page's reach. A browser does not let a page set the ``Host``
    header, but the host the page asks for lands in it, so a page served
    from a name that resolves to the loopback interface -- DNS rebinding
    -- arrives here with its ``Host`` and its ``Origin`` in agreement, and
    a rule that compared the two alone would take it for the API's own
    page. Hence the loopback name is required of the host the request was
    addressed to, and not only of the origin it claims.

    A request with no ``Host`` header, or with one that is not a host
    name at all, is not refused outright. The host the rule compares
    against is the one in the request's URL, and Starlette's ``URL``
    falls back to the address the connection was accepted on -- the
    transport address -- when the header is missing or does not parse,
    so the comparison is then made against the transport address and not
    against a name the caller chose. The transport address is the
    server's own and not the caller's to choose, so the rule keeps its
    footing either way: the request counts as the API's own only when
    that address is a loopback name and the origin agrees with it, and a
    mutating request from a foreign origin is refused with a 403 and the
    change does not happen, whatever the ``Host`` header says or does
    not say.

    The scheme and the port have to be the API's own: a page served on
    another port of the loopback interface is another server, and its page
    is not a page this API serves. The host may be spelled either way
    round -- ``127.0.0.1`` and ``localhost`` name the same machine, and
    the API is one machine -- so both spellings are the API's own too, as
    long as the port is the same one.

    An API that is reached at a name which is not a loopback name -- a
    LAN address, say -- has no own origin by this rule. That is
    deliberate: the operator names that origin in
    ``LMEDITOR_ALLOWED_ORIGINS``, which is an exact-origin allowance.
    """
    parts = _origin_parts(origin)
    served = _origin_parts(str(request.url))
    if parts is None or served is None:
        return False
    scheme, host, port = parts
    served_scheme, served_host, served_port = served
    if scheme != served_scheme or port != served_port:
        return False
    if not _is_loopback_host(served_host):
        # The request was not addressed to the loopback interface at all,
        # so this is not the API's own origin, whatever the page claims.
        return False
    return host == served_host or _is_loopback_host(host)


def _origin_parts(origin: str) -> tuple[str, str, int] | None:
    """The scheme, the host and the port of an origin.

    ``None`` when the text is not an origin at all: ``null``, a file
    path, or anything else a browser would never send as one. The port of
    an origin that does not spell one out is the port of its scheme.
    """
    try:
        parsed = urlsplit(origin)
        port = parsed.port
    except ValueError:
        return None
    host = parsed.hostname
    if not parsed.scheme or not host:
        return None
    if port is None:
        port = 443 if parsed.scheme.lower() == "https" else 80
    return parsed.scheme.lower(), host.lower(), port


def _is_loopback_host(host: str) -> bool:
    """Whether this host is one of the names the loopback interface answers to.

    Only the canonical names count: ``localhost``, ``127.0.0.1`` and
    ``::1``. A browser reaches the loopback interface by one of those
    three names, and each of them is the machine itself. The rest of
    ``127.0.0.0/8`` names other addresses of the same machine, and a
    server on one of them is another server -- so for this rule it is not
    the same machine, and its page is not a page this API serves.
    """
    return host in _LOOPBACK_HOSTS


def _is_preflight(request: Request) -> bool:
    """Whether this is a browser asking what it may do before it does it."""
    return (
        request.method == "OPTIONS"
        and "access-control-request-method" in request.headers
    )


def _preflight_response(origin: str, request: Request) -> Response:
    """The answer to a browser's question about what it may do.

    The answer names the origin the page may use and the methods it may
    use. An origin that is not allowed is answered with a refusal and no
    origin header at all, so a page on the network learns nothing about
    this API beyond the fact that it may not.
    """
    headers = {
        "Vary": "Origin",
        "Access-Control-Allow-Methods": ", ".join(_CORS_METHODS),
        "Access-Control-Max-Age": str(_CORS_MAX_AGE),
    }
    requested_headers = request.headers.get("access-control-request-headers")
    if requested_headers:
        headers["Access-Control-Allow-Headers"] = requested_headers
    failures: list[str] = []
    if not _origin_allowed(origin, request):
        failures.append("origin")
    requested_method = request.headers.get("access-control-request-method", "")
    if requested_method and requested_method not in _CORS_METHODS:
        failures.append("method")
    if failures:
        return PlainTextResponse(
            "Disallowed CORS " + ", ".join(failures),
            status_code=400,
            headers=headers,
        )
    headers["Access-Control-Allow-Origin"] = origin
    return PlainTextResponse("OK", status_code=200, headers=headers)


#: How deep the details of a failure may be walked.
#:
#: The details are the client's to read, and a client reads a handful of
#: levels. A structure that is deeper than this is cut short rather than
#: walked: the walk happens inside an exception handler, and a structure
#: that never ends would turn a handled failure into a crash of its own.
_MAX_DETAIL_DEPTH: int = 10

#: What stands in for the part of the details that was cut short.
_DETAIL_TRUNCATED: str = "<truncated>"


def safe_details(details: Any) -> Any:
    """The details as they may leave the server.

    The shape and the keys are the client's to rely on, so they are kept:
    a client that keys on ``reason`` still finds ``reason`` there. The keys
    are held to the same rules as the values, though: a key that carries
    the machine's own detail -- an absolute path, the tier's own rendering
    of a map of details, a class of the JVM -- would name the server's own
    disk in the name the client reads. Such a key is not dropped and not
    reworded: the machine's detail in it is replaced, exactly as it is in
    the value beside it.

    Only the values are otherwise touched, and only where a value carries
    the machine's own detail. Every other value is passed through as it
    is, with its whitespace and its wording untouched.

    The machine's own detail is taken out by the one sanitiser the server
    has (:func:`~lmeditor.server.routes_helpers.safe_detail_value`), so a
    detail is held to the same rules as the message beside it: a path under
    a root the server was never told about is a path of the machine just
    the same, and a class of the JVM is a class of the machine just the
    same.

    A structure that refers to itself, or that is deeper than
    ``_MAX_DETAIL_DEPTH``, is cut short rather than walked. The walk
    happens inside an exception handler, so a structure that never ends
    would turn a handled failure into a crash of its own.
    """
    return _safe_details(details, depth=0, seen=())


def _safe_details(details: Any, *, depth: int, seen: tuple[int, ...]) -> Any:
    """The details as they may leave the server, walked one level at a time.

    ``seen`` holds the identity of the containers on the way down, so a
    structure that refers to itself is recognised as such and cut short
    instead of being walked for ever.
    """
    if isinstance(details, str):
        return safe_detail_value(details)
    if isinstance(details, dict):
        if depth >= _MAX_DETAIL_DEPTH or id(details) in seen:
            return _DETAIL_TRUNCATED
        below = seen + (id(details),)
        return {
            _safe_detail_key(key): _safe_details(value, depth=depth + 1, seen=below)
            for key, value in details.items()
        }
    if isinstance(details, list):
        if depth >= _MAX_DETAIL_DEPTH or id(details) in seen:
            return _DETAIL_TRUNCATED
        below = seen + (id(details),)
        return [
            _safe_details(value, depth=depth + 1, seen=below) for value in details
        ]
    if isinstance(details, tuple):
        if depth >= _MAX_DETAIL_DEPTH or id(details) in seen:
            return _DETAIL_TRUNCATED
        below = seen + (id(details),)
        return tuple(
            _safe_details(value, depth=depth + 1, seen=below) for value in details
        )
    return details


def _safe_detail_key(key: Any) -> Any:
    """One key of the details of a failure, as it may leave the server.

    A key names the server's own thing as much as a value does: a key that
    carried an absolute path would put the machine's own disk into the name
    the client reads. The key is therefore held to the same rule as the
    value beside it, and the machine's detail in it is replaced rather than
    the key dropped -- so a client that keys on a detail still finds the
    detail there, under a name that describes no folder.

    A key that is not a string is left as it is: it carries no spelling and
    so no machine detail, and the client's own key must survive.

    Args:
        key: One key of the details of a failure.

    Returns:
        The key, with the machine's own detail taken out of it.
    """
    if isinstance(key, str):
        return safe_detail_value(key)
    return key


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan manager for graceful resource setup and teardown."""
    # Startup: install the language model the harness drafts with.
    #
    # Every generative path in LMeditor needs one, and the app refuses to
    # fabricate prose in its absence. A missing or invalid configuration is
    # therefore reported through ``GET /api/v1/health`` rather than being
    # allowed to surface later as a confusing per-section failure.
    from lmeditor.llm import configure_lm

    configure_lm()

    yield

    # Shutdown: release the model so a reload starts from a clean state.
    from lmeditor.llm import reset

    reset()


def create_app() -> FastAPI:
    """Factory creating and configuring the LMeditor FastAPI application."""
    documented = expose_docs()
    app = FastAPI(
        title="LMeditor Agentic Core Gateway",
        description="High-throughput FastAPI gateway serving LangGraph cyclic state machine and NLI verification SSE streams.",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if documented else None,
        redoc_url="/redoc" if documented else None,
        openapi_url="/openapi.json" if documented else None,
    )

    # CORS for the browser tools that reach this gateway.
    #
    # The origins are named, not wildcards, and credentials are off: the API
    # has no cookie and no token of its own, so credentials would buy
    # nothing and a wildcard would give every page on the network the
    # operator's reach. The list here is the origins the operator named;
    # the API's own origin is not in it, because it is not a constant, and
    # it is answered by the middleware below. The desktop client is not a
    # browser and is not affected by any of this.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins(),
        allow_credentials=False,
        allow_methods=list(_CORS_METHODS),
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def refuse_a_foreign_origin(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """Refuse a request from a page this API does not serve.

        CORS decides whether a browser lets a page READ the answer; it never
        stops the request from reaching the server. So a request that names a
        folder, starts a run or writes a note is refused outright when it
        comes from an origin this API does not serve, and the side effect
        never happens.

        The desktop client sends no ``Origin`` header at all and is not
        affected, and a page this API serves on the loopback interface is
        its own origin.

        The CORS answer is given here as well, because the origin this API
        is served from is not a constant: it is the port the request was
        addressed to, and a page the API serves under the other spelling of
        the loopback address has to be answered with its own origin too.
        """
        origin = request.headers.get("origin")
        if origin is not None:
            if (
                request.method in _MUTATING_METHODS
                and not _origin_allowed(origin, request)
            ):
                return JSONResponse(
                    status_code=status.HTTP_403_FORBIDDEN,
                    content={
                        "detail": (
                            f"The origin '{origin}' is not allowed to change "
                            "anything on this server."
                        )
                    },
                )
            if _is_preflight(request) and origin not in allowed_origins():
                # The allowlist cannot hold the API's own origin -- it is
                # not a constant -- so the answer to the browser's own
                # question is given here.
                return _preflight_response(origin, request)
        response = await call_next(request)
        if (
            origin is not None
            and origin not in allowed_origins()
            and _is_own_origin(origin, request)
        ):
            response.headers["access-control-allow-origin"] = origin
            response.headers.add_vary_header("Origin")
        return response

    # Domain exception handlers
    @app.exception_handler(CitationHallucinationError)
    async def citation_hallucination_handler(
        request: Request, exc: CitationHallucinationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "error": "CitationHallucinationError",
                "message": safe_message(exc.message),
                "details": safe_details(exc.details),
            },
        )

    @app.exception_handler(UngroundedClaimError)
    async def ungrounded_claim_handler(
        request: Request, exc: UngroundedClaimError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "error": "UngroundedClaimError",
                "message": safe_message(exc.message),
                "details": safe_details(exc.details),
            },
        )

    @app.exception_handler(StateGraphDeadlockError)
    async def deadlock_handler(
        request: Request, exc: StateGraphDeadlockError
    ) -> JSONResponse:
        # The details of a deadlock are the server's own state: they stay in
        # the log, and the client is told that the run did not finish.
        logger.exception("The workflow deadlocked: %s", exc.message)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": "StateGraphDeadlockError",
                "message": safe_message(exc.message),
            },
        )

    @app.exception_handler(VerificationError)
    async def verification_error_handler(
        request: Request, exc: VerificationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "error": "VerificationError",
                "message": safe_message(exc.message),
                "details": safe_details(exc.details),
            },
        )

    @app.exception_handler(WorkflowError)
    async def workflow_error_handler(
        request: Request, exc: WorkflowError
    ) -> JSONResponse:
        # The details of a failed run are the server's own state: they stay
        # in the log, and the client is told that the run failed.
        logger.exception("The workflow failed: %s", exc.message)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": "WorkflowError",
                "message": safe_message(exc.message),
            },
        )

    @app.exception_handler(LMeditorError)
    async def generic_lmeditor_error_handler(
        request: Request, exc: LMeditorError
    ) -> JSONResponse:
        # A failure that reached this far is the server's own fault, and its
        # message may carry the layout of the machine -- an absolute path, a
        # JVM's own rendering of a map of details, the class name the JVM
        # died with. The client is told what failed, in words that do not
        # describe the disk; the whole of it, paths and all, goes to the log.
        logger.exception("The request failed: %s", exc.message, exc_info=exc)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": "LMeditorError",
                "message": safe_detail(exc),
            },
        )

    @app.exception_handler(ValidationError)
    async def validation_error_handler(
        request: Request, exc: ValidationError
    ) -> JSONResponse:
        # A value that does not satisfy a constraint is the caller's mistake,
        # not a fault of the server, so it is a 400 rather than a 500.
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "error": "ValidationError",
                "message": safe_message(exc.message),
            },
        )

    # ------------------------------------------------------------------------
    # Targeted editing, fact checking, and the book library
    # ------------------------------------------------------------------------
    #
    # These carry their own status codes because they answer a different
    # question from a crash: a missing section is a 404, an unusable
    # revision is a 422, and a missing language model is a 503 that says
    # "configure the model", not "the server is broken".

    @app.exception_handler(EditTargetNotFoundError)
    async def edit_target_not_found_handler(
        request: Request, exc: EditTargetNotFoundError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={
                "error": "EditTargetNotFoundError",
                "message": safe_message(exc.message),
                "details": safe_details(exc.details),
            },
        )

    @app.exception_handler(InlineEditRejectedError)
    async def inline_edit_rejected_handler(
        request: Request, exc: InlineEditRejectedError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "error": "InlineEditRejectedError",
                "message": safe_message(exc.message),
                "details": safe_details(exc.details),
            },
        )

    @app.exception_handler(LMNotConfiguredError)
    async def lm_not_configured_handler(
        request: Request, exc: LMNotConfiguredError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "error": "LMNotConfiguredError",
                "message": safe_message(exc.message),
                "details": safe_details(exc.details),
            },
        )

    @app.exception_handler(FactCheckError)
    async def fact_check_error_handler(
        request: Request, exc: FactCheckError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "error": "FactCheckError",
                "message": safe_message(exc.message),
                "details": safe_details(exc.details),
            },
        )

    @app.exception_handler(WebEvidenceError)
    async def web_evidence_error_handler(
        request: Request, exc: WebEvidenceError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content={
                "error": "WebEvidenceError",
                "message": safe_message(exc.message),
                "details": safe_details(exc.details),
            },
        )

    @app.exception_handler(BookNotFoundError)
    async def book_not_found_handler(request: Request, exc: BookNotFoundError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={
                "error": "BookNotFoundError",
                "message": safe_message(exc.message),
                "details": safe_details(exc.details),
            },
        )

    @app.exception_handler(SectionNotFoundError)
    async def section_not_found_handler(
        request: Request, exc: SectionNotFoundError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={
                "error": "SectionNotFoundError",
                "message": safe_message(exc.message),
                "details": safe_details(exc.details),
            },
        )

    @app.exception_handler(LibraryError)
    async def library_error_handler(request: Request, exc: LibraryError) -> JSONResponse:
        # A book id or section id that fails the identifier policy is the
        # caller's mistake, not a server fault, so it is a 400 rather than
        # a 500. The reason the library failed is a different matter: it
        # names the files and the tier's own words, and the caller is told
        # only what failed. The whole of it goes to the log, where the
        # operator can read it, as the other handlers' reasons do.
        logger.exception("The library could not be used: %s", exc.message, exc_info=exc)
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "error": "LibraryError",
                "message": safe_message(exc.message),
                "details": safe_details(exc.details),
            },
        )

    # Register API routers
    app.include_router(router)

    return app


app = create_app()
