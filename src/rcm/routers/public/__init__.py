"""Public-facing API mounted at `/api/*`.

These endpoints serve the user-facing UI that lives at `frontend/src/pages/`.
Distinct from `routers.dev` which serves the developer console at `/api/dev/*`.

Response shapes are pinned to what the v1 frontend's `services/api.js`
expects — keep them stable or update both sides together.
"""

from fastapi import APIRouter

from rcm.routers.public.edi import router as edi_router
from rcm.routers.public.claims import router as claims_router
from rcm.routers.public.predictions import router as predictions_router
from rcm.routers.public.ml import router as ml_router
from rcm.routers.public.recommendations import router as recommendations_router

router = APIRouter(tags=["public"])
router.include_router(edi_router,             prefix="/edi",            tags=["edi"])
router.include_router(claims_router,          prefix="/claims",         tags=["claims"])
router.include_router(predictions_router,     prefix="/predictions",    tags=["predictions"])
router.include_router(ml_router,              prefix="/ml",             tags=["ml"])
router.include_router(recommendations_router, prefix="/recommendations", tags=["recommendations"])
