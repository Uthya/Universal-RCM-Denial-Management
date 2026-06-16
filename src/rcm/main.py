"""FastAPI application entry point.

The app mounts two route families:
    /api/dev/*     — developer console endpoints (read-mostly, see CR-038)
    /api/*         — future production endpoints (Phase 4+; not yet built)

CORS is open to http://localhost:5173 (Vite dev server) and nothing else by
default. Configured via settings.CORS_ORIGINS for additional origins.

Audit-actor injection: every /api/dev/* request runs in a session that has
`SET LOCAL audit.user = 'dev-console'` so audit_log rows record where the
write came from. See CR-008 audit trigger semantics.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from rcm.core.config import settings
from rcm.core.logging import configure_logging, get_logger
from rcm.routers.dev import router as dev_router
from rcm.routers.public import router as public_router

logger = get_logger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    configure_logging()
    logger.info(
        "rcm.api starting",
        env_url=settings.database_url_redacted(),
        debug=settings.DEBUG,
        rag_enabled=settings.RAG_ENABLED,
    )
    yield
    logger.info("rcm.api shutdown")


def create_app() -> FastAPI:
    app = FastAPI(
        title="RCM v2 — Developer Console API",
        version="2.0.0",
        description=(
            "Developer-facing observability + verification API. "
            "Mount under /api/dev/*. NOT a user-facing surface."
        ),
        lifespan=_lifespan,
        # docs only in dev — production would gate behind auth
        docs_url="/docs" if settings.DEBUG else None,
        redoc_url=None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    app.include_router(dev_router, prefix="/api/dev")
    app.include_router(public_router, prefix="/api")
    return app


app = create_app()
