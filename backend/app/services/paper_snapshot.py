"""用纸档旁路快照（sidecar）拼装辅助。

职责边界：
- 旁路真相只在“显式导出”那一刻生成：从主库 calc_runs 只读取数，
  整体拼装、整体重写旁路文件。
- 测算保存（/api/estimate save=true）只写主库，绝不触碰旁路文件；
  因此主库新增用纸档不会拖着旁路文件变化。
- 本模块只做：取数读取 -> 纯字段拼装 build_snapshot_row -> 落盘/读盘
  与全文校验和。不包含任何面积计算，面积以引擎已落库的结果为准。
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from app.config import DATA_DIR
from app.db import connect

# 旁路快照文件：与主库 app.db 同目录的 JSONL sidecar。
SNAPSHOT_PATH = DATA_DIR / "paper_snapshot.jsonl"


def _fetch_saved_runs() -> list[dict]:
    """只读：从主库取已保存测算，按 run 升序，供导出快照使用。"""
    c = connect()
    try:
        rows = c.execute(
            """SELECT r.id, r.box_id, r.overlap, r.result_json, r.created_at,
                      b.name AS box_name
               FROM calc_runs r
               LEFT JOIN boxes b ON b.id = r.box_id
               ORDER BY r.id ASC""",
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        c.close()


def build_snapshot_row(run: dict) -> dict:
    """纯函数：把一条主库测算记录拼装为旁路快照的 JSON 字段。

    面积字段直接取自已落库的引擎结果（result_json.paper_m2），
    不在此重算，保证面积公式只有一个出处。
    """
    result = json.loads(run["result_json"]) if isinstance(run.get("result_json"), str) else (run.get("result_json") or {})
    return {
        "run_id": int(run["id"]),
        "box_id": int(run["box_id"]),
        "box_name": run.get("box_name"),
        "paper_m2": result.get("paper_m2"),
        "box_surface": result.get("box_surface"),
        "overlap": result.get("overlap", run.get("overlap")),
        "created_at": run.get("created_at"),
    }


def serialize_snapshot(rows: list[dict]) -> bytes:
    """把快照行序列化为 JSONL 全文（每行一个 JSON，末尾换行）。"""
    return b"".join(
        (json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        for row in rows
    )


def checksum_of(content: bytes) -> str:
    """全文校验和：对旁路文件的完整字节取 sha256。"""
    return hashlib.sha256(content).hexdigest()


def export_snapshot(path: Path | str | None = None) -> dict:
    """显式导出：从主库读取 -> 拼装 -> 原子整体重写旁路文件。

    返回路径、行数、首条 paper_m2 与全文校验和。主库新行只有经过下一次
    显式导出才会进入文件，两次导出之间文件保持冻结。
    """
    target = Path(path) if path is not None else SNAPSHOT_PATH
    runs = _fetch_saved_runs()
    rows = [build_snapshot_row(r) for r in runs]
    content = serialize_snapshot(rows)
    digest = checksum_of(content)

    # 原子落盘：同目录临时文件 + os.replace，避免读到半写状态。
    tmp = target.with_name(target.name + ".tmp")
    with open(tmp, "wb") as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, target)

    return {
        "path": str(target),
        "rows": len(rows),
        "first_paper_m2": rows[0]["paper_m2"] if rows else None,
        "sha256": digest,
    }


def read_snapshot(path: Path | str | None = None) -> dict:
    """读盘核验：直接按磁盘字节解析旁路文件并重算全文校验和。"""
    target = Path(path) if path is not None else SNAPSHOT_PATH
    with open(target, "rb") as f:
        content = f.read()
    rows = [json.loads(line) for line in content.decode("utf-8").splitlines() if line.strip()]
    return {
        "path": str(target),
        "rows": len(rows),
        "items": rows,
        "first_paper_m2": rows[0]["paper_m2"] if rows else None,
        "sha256": checksum_of(content),
    }
