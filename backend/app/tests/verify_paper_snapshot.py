"""磁盘核验入口：用纸档旁路落盘与冻结检查。

运行方式（在 backend/ 下）：
    python -m app.tests.verify_paper_snapshot

流程：
  1. 写第一单测算并记下 paper_m2，触发导出，旁路文件须可读回该面积与全文 sha256；
  2. 再写另一礼盒测算但故意不导出，主库用纸档可多一行；
  3. 重读旁路文件，行数、首条 paper_m2、全文校验和必须仍停在第一次导出。

任一对不上即以非零退出，并标明是 [导出侧漂移] 还是 [主库侧漂移]。
本脚本只观察旁路快照与主库的关系，不改动面积公式。
"""
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def run() -> int:
    # 每次核验使用独立 DATA_DIR，保证连续执行互不污染。
    data_dir = tempfile.mkdtemp(prefix="paper_snapshot_verify_")
    os.environ["DATA_DIR"] = data_dir

    from fastapi.testclient import TestClient

    from app.main import app
    from app.repositories import history
    from app.services.paper_snapshot import SNAPSHOT_PATH

    try:
        with TestClient(app) as client:
            # ---- 第一单：写入主库并触发导出 ----
            r1 = client.post("/api/estimate", json={"box_id": 1, "save": True})
            if r1.status_code != 200:
                print(f"[主库侧漂移] 第一单测算失败 HTTP {r1.status_code}: {r1.text}")
                return 1
            m2_first = r1.json()["paper_m2"]

            db_rows = history.list_runs()
            if len(db_rows) != 1:
                print(f"[主库侧漂移] 首单后主库用纸档应有 1 行，实际 {len(db_rows)} 行")
                return 1

            ex = client.post("/api/export/paper-runs")
            if ex.status_code != 200:
                print(f"[导出侧漂移] 导出请求失败 HTTP {ex.status_code}: {ex.text}")
                return 1
            if not SNAPSHOT_PATH.exists():
                print(f"[导出侧漂移] 导出后磁盘上找不到旁路文件: {SNAPSHOT_PATH}")
                return 1

            body1 = SNAPSHOT_PATH.read_bytes()
            sum1 = hashlib.sha256(body1).hexdigest()
            lines1 = body1.decode("utf-8").splitlines()
            if ex.json().get("checksum") != sum1:
                print("[导出侧漂移] 导出返回的校验和与磁盘文件全文 sha256 不一致")
                return 1
            if len(lines1) != 1:
                print(f"[导出侧漂移] 首次导出旁路文件应有 1 行，实际 {len(lines1)} 行")
                return 1
            rec1 = json.loads(lines1[0])
            if rec1.get("paper_m2") != m2_first:
                print(
                    f"[导出侧漂移] 旁路首条 paper_m2={rec1.get('paper_m2')} "
                    f"与测算结果 {m2_first} 不符"
                )
                return 1

            # ---- 第二单：另一礼盒，只写主库，故意不导出 ----
            r2 = client.post("/api/estimate", json={"box_id": 2, "save": True})
            if r2.status_code != 200:
                print(f"[主库侧漂移] 第二单测算失败 HTTP {r2.status_code}: {r2.text}")
                return 1
            m2_second = r2.json()["paper_m2"]
            if m2_second == m2_first:
                print("[主库侧漂移] 两单 paper_m2 相同，无法区分快照是否被新行拖动")
                return 1

            db_rows2 = history.list_runs()
            if len(db_rows2) != 2:
                print(
                    f"[主库侧漂移] 第二单后主库用纸档应有 2 行，实际 {len(db_rows2)} 行"
                )
                return 1

            # ---- 不做任何导出，直接重读旁路文件：必须冻结在第一次导出 ----
            body1b = SNAPSHOT_PATH.read_bytes()
            sum1b = hashlib.sha256(body1b).hexdigest()
            lines1b = body1b.decode("utf-8").splitlines()
            if len(lines1b) != 1:
                print(
                    f"[导出侧漂移] 未再导出但旁路行数从 1 变为 {len(lines1b)}，"
                    "文件被主库新行拖着走了"
                )
                return 1
            rec1b = json.loads(lines1b[0])
            if rec1b.get("paper_m2") != m2_first:
                print(
                    f"[导出侧漂移] 未再导出但首条 paper_m2 从 {m2_first} "
                    f"变为 {rec1b.get('paper_m2')}"
                )
                return 1
            if sum1b != sum1:
                print(f"[导出侧漂移] 未再导出但全文校验和变化: {sum1} -> {sum1b}")
                return 1

        print(f"OK 首次导出落盘: 行数=1 首条 paper_m2={m2_first} sha256={sum1}")
        print(
            f"OK 旁路冻结: 主库 {len(db_rows2)} 行而旁路仍 1 行，"
            f"第二单 paper_m2={m2_second} 未导出即未落盘 ({SNAPSHOT_PATH})"
        )
        return 0
    finally:
        shutil.rmtree(data_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(run())
