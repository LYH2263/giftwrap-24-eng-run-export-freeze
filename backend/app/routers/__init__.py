from fastapi import APIRouter
from app.routers import boxes, estimates, export, history_router, papers, settings
api_router = APIRouter(prefix="/api")
api_router.include_router(boxes.router)
api_router.include_router(papers.router)
api_router.include_router(estimates.router)
api_router.include_router(export.router)
api_router.include_router(history_router.router)
api_router.include_router(settings.router)
