from fastapi import APIRouter
from app.routers import boxes, estimates, history_router, paper_snapshot, papers, settings
api_router = APIRouter(prefix="/api")
api_router.include_router(boxes.router)
api_router.include_router(papers.router)
api_router.include_router(estimates.router)
api_router.include_router(history_router.router)
api_router.include_router(settings.router)
api_router.include_router(paper_snapshot.router)
