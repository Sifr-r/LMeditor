"""FastAPI application configuration with CORS, lifespan handlers, and domain error mapping."""

from contextlib import asynccontextmanager
from typing import AsyncGenerator
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from lmeditor.core.exceptions import (
    CitationHallucinationError,
    LMeditorError,
    StateGraphDeadlockError,
    UngroundedClaimError,
    VerificationError,
    WorkflowError,
)
from lmeditor.server.routes import router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan manager for graceful resource setup and teardown."""
    # Startup actions
    yield
    # Shutdown actions


def create_app() -> FastAPI:
    """Factory creating and configuring the LMeditor FastAPI application."""
    app = FastAPI(
        title="LMeditor Agentic Core Gateway",
        description="High-throughput FastAPI gateway serving LangGraph cyclic state machine and NLI verification SSE streams.",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Configure CORS for Flutter Desktop and web clients
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Domain exception handlers
    @app.exception_handler(CitationHallucinationError)
    async def citation_hallucination_handler(
        request: Request, exc: CitationHallucinationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "error": "CitationHallucinationError",
                "message": exc.message,
                "details": exc.details,
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
                "message": exc.message,
                "details": exc.details,
            },
        )

    @app.exception_handler(StateGraphDeadlockError)
    async def deadlock_handler(
        request: Request, exc: StateGraphDeadlockError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": "StateGraphDeadlockError",
                "message": exc.message,
                "details": exc.details,
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
                "message": exc.message,
                "details": exc.details,
            },
        )

    @app.exception_handler(WorkflowError)
    async def workflow_error_handler(
        request: Request, exc: WorkflowError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": "WorkflowError",
                "message": exc.message,
                "details": exc.details,
            },
        )

    @app.exception_handler(LMeditorError)
    async def generic_lmeditor_error_handler(
        request: Request, exc: LMeditorError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": "LMeditorError",
                "message": exc.message,
                "details": exc.details,
            },
        )

    # Register API routers
    app.include_router(router)

    return app


app = create_app()
