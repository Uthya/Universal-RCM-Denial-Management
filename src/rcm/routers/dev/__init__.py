"""Developer console endpoints — observability + verification + safe debug.

Conventions (CR-038):
    * Read endpoints (GET) have no side effects, no auth required for now
      (local-only deployment per spec §8).
    * Write endpoints (POST) require `?confirm=true` query param. Returning
      400 without it discourages accidental triggers from curl / browser
      history.
    * The SQL console is the ONLY write surface that can run arbitrary SQL;
      it enforces SELECT-only + statement_timeout + row limit server-side.
    * Every request is logged under `actor='dev-console'` so audit_log can
      trace what the UI wrote.
    * Errors return {detail: "..."} with appropriate 4xx/5xx.
    * List endpoints support limit/offset; response shape is
      {items: [...], total: N, limit: N, offset: N}.

The dev console is NEVER deployed alongside the production user UI.
"""

from fastapi import APIRouter, HTTPException, Query


# ---- Confirmation guard for write endpoints ----------------------------
# Defined FIRST so subrouter modules can import it from this package.

class ConfirmationRequired(HTTPException):
    """Raised by write endpoints when ?confirm=true is missing."""

    def __init__(self, action: str):
        super().__init__(
            status_code=400,
            detail=(
                f"Operation {action!r} requires explicit confirmation. "
                "Pass ?confirm=true to proceed. This is a developer endpoint "
                "with side effects."
            ),
        )


def require_confirm(confirm: bool, action: str) -> None:
    """Helper for write endpoints. Call at the top of the route function:

        @router.post("/refresh-mv/{name}")
        async def refresh(name: str, confirm: bool = Query(False)):
            require_confirm(confirm, f"refresh-mv:{name}")
            ...
    """
    if not confirm:
        raise ConfirmationRequired(action)


# ---- Subrouter assembly (defined after the helpers they import) --------

from rcm.routers.dev.env import router as env_router                    # noqa: E402
from rcm.routers.dev.db import router as db_router                      # noqa: E402
from rcm.routers.dev.telemetry import router as telemetry_router        # noqa: E402
from rcm.routers.dev.uploads import router as uploads_router            # noqa: E402
from rcm.routers.dev.models import router as models_router              # noqa: E402
from rcm.routers.dev.jobs import router as jobs_router                  # noqa: E402
from rcm.routers.dev.edi import router as edi_router                    # noqa: E402
from rcm.routers.dev.claims import router as claims_router              # noqa: E402

router = APIRouter(tags=["dev-console"])
router.include_router(env_router,       prefix="/env",       tags=["dev/env"])
router.include_router(db_router,        prefix="/db",        tags=["dev/db"])
router.include_router(telemetry_router, prefix="/telemetry", tags=["dev/telemetry"])
router.include_router(uploads_router,   prefix="/uploads",   tags=["dev/uploads"])
router.include_router(models_router,    prefix="/models",    tags=["dev/models"])
router.include_router(jobs_router,      prefix="/jobs",      tags=["dev/jobs"])
router.include_router(edi_router,       prefix="/edi",       tags=["dev/edi"])
router.include_router(claims_router,    prefix="/claims",    tags=["dev/claims"])
