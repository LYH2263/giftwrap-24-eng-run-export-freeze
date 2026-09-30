"""用纸档旁路快照导出 HTTP 把手。

路由保持薄：只负责接收“显式导出”动作并回传结果，
取数 / JSON 字段拼装 / 校验和 / 落盘全部在 service 层完成，
不在路由函数体内联拼装逻辑。
"""
from fastapi import APIRouter

from app.services import paper_snapshot as service

router = APIRouter(prefix="/paper-snapshot", tags=["paper_snapshot"])


@router.post("/export")
def export_paper_snapshot():
    return service.export_snapshot()
