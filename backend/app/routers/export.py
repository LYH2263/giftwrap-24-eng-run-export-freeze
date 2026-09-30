"""用纸档旁路导出 HTTP 把手：只做转发，字段拼装全部在服务层。"""
from fastapi import APIRouter

from app.services import paper_snapshot

router = APIRouter()


@router.post("/export/paper-runs")
def export_paper_runs():
    return paper_snapshot.export_runs()
