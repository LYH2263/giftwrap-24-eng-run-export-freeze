"""用纸档旁路快照：导出落盘与冻结读取。

旁路文件的真相来源是**导出动作发生那一刻的快照**，不是主库实时状态：
主库 calc_runs 之后再新增行，不会反向拖动本文件，只有再次调用
export_runs() 才会刷新。JSON 字段的拼装只允许出现在本模块，
路由/核验脚本均不得内联拼装。
"""
import hashlib
import json
import os
from datetime import datetime, timezone

from app.config import DATA_DIR
from app.db import connect

SNAPSHOT_PATH = DATA_DIR / "paper_snapshot.jsonl"


def checksum(body: str | bytes) -> str:
    """全文校验和：对旁路文件的完整字节做 sha256。"""
    if isinstance(body, str):
        body = body.encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def build_paper_record(run) -> dict:
    """把一条主库测算行拼装成旁路快照的 JSON 字段（唯一拼装入口）。"""
    result = run["result_json"]
    if isinstance(result, str):
        result = json.loads(result)
    return {
        "run_id": run["id"],
        "box_id": run["box_id"],
        "box_name": run["box_name"],
        "overlap": run["overlap"],
        "box_surface": result.get("box_surface"),
        "paper_m2": result.get("paper_m2"),
        "exported_at": datetime.now(timezone.utc).isoformat(),
    }


def render_snapshot(records: list[dict]) -> str:
    """JSONL：一条用纸档一行，行序即导出快照顺序。"""
    return "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records)


def _load_runs():
    """导出时刻对主库取一次定格数据，此后与主库再无牵连。"""
    c = connect()
    try:
        return c.execute(
            """SELECT r.*, b.name box_name
                 FROM calc_runs r
                 LEFT JOIN boxes b ON b.id = r.box_id
                ORDER BY r.id ASC"""
        ).fetchall()
    finally:
        c.close()


def export_runs() -> dict:
    """触发一次导出：定格主库用纸档并整体覆盖旁路文件。"""
    records = [build_paper_record(r) for r in _load_runs()]
    body = render_snapshot(records)
    tmp = SNAPSHOT_PATH.with_name(SNAPSHOT_PATH.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(body)
    os.replace(tmp, SNAPSHOT_PATH)
    return {"path": str(SNAPSHOT_PATH), "rows": len(records), "checksum": checksum(body)}


def read_snapshot() -> dict:
    """从磁盘旁路文件原样读回；文件不存在视为空快照。"""
    if not SNAPSHOT_PATH.exists():
        return {"path": str(SNAPSHOT_PATH), "rows": 0, "records": [], "checksum": None}
    raw = SNAPSHOT_PATH.read_bytes()
    records = [json.loads(line) for line in raw.decode("utf-8").splitlines()]
    return {
        "path": str(SNAPSHOT_PATH),
        "rows": len(records),
        "records": records,
        "checksum": checksum(raw),
    }
