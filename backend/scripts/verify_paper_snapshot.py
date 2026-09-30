#!/usr/bin/env python3
"""用纸档旁路落盘与冻结核验入口。

流程（全程走真实 HTTP 把手，旁路数据一律直接读磁盘核验）：
  1) 写入第一单测算并记下 paper_m2；
  2) 触发 POST /api/paper-snapshot/export，读旁路文件，
     核验行数 / 首条 paper_m2 / 全文 sha256（导出侧）；
  3) 写入另一礼盒的测算但【不导出】；
  4) 主库用纸档列表（/api/runs）应多一行；
  5) 重读旁路文件：行数、首条 paper_m2、全文 sha256 必须仍停在第一次导出，
     连原始字节都不得变化（主库侧漂移检查）。

退出码：全部通过为 0；任何不符为 1，并标明
  [EXPORT_SIDE_DRIFT] 导出侧漂移（文件没停在导出快照 / 面积或校验和对不上）
  [DB_SIDE_DRIFT]     主库侧漂移（主库未按预期增长，或新行拖着旁路文件变）

每次运行使用独立临时 DATA_DIR，连续执行互不影响。
用法：在 backend/ 目录下  python3 scripts/verify_paper_snapshot.py
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

# 必须在导入 app.config 之前指向隔离数据目录，避免碰到真实库与旁路文件。
_TMP_DATA = tempfile.mkdtemp(prefix="paper_snapshot_verify_")
os.environ["DATA_DIR"] = _TMP_DATA
_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.services import paper_snapshot as service  # noqa: E402

EXIT_OK = 0
EXIT_DRIFT = 1


class DriftError(Exception):
    def __init__(self, side: str, message: str):
        super().__init__(message)
        self.side = side  # EXPORT_SIDE_DRIFT | DB_SIDE_DRIFT


def _raw_snapshot() -> tuple[bytes, list[dict]]:
    """直接按磁盘字节读取旁路文件，解析 JSONL 行。"""
    path = Path(service.SNAPSHOT_PATH)
    content = path.read_bytes()
    rows = [json.loads(line) for line in content.decode("utf-8").splitlines() if line.strip()]
    return content, rows


def main() -> int:
    keep_evidence = True
    try:
        with TestClient(app) as client:
            # ---- 步骤 1：写入第一单（书型盒 box_id=1），记下面积 ----
            r1 = client.post("/api/estimate", json={"box_id": 1, "save": True})
            if r1.status_code != 200:
                raise DriftError("DB_SIDE_DRIFT", f"首单写入失败 HTTP {r1.status_code}: {r1.text}")
            paper_m2_1 = r1.json()["paper_m2"]
            print(f"[1] 写入第一单 box_id=1  paper_m2={paper_m2_1}")

            # ---- 步骤 2：显式导出，随后直接读盘核验（导出侧） ----
            re = client.post("/api/paper-snapshot/export")
            if re.status_code != 200:
                raise DriftError("EXPORT_SIDE_DRIFT", f"导出把手失败 HTTP {re.status_code}: {re.text}")
            export_meta = re.json()
            sidecar = Path(export_meta["path"])
            if not sidecar.exists():
                raise DriftError("EXPORT_SIDE_DRIFT", f"导出后旁路文件不存在: {sidecar}")

            frozen_bytes, frozen_rows = _raw_snapshot()
            disk_checksum = hashlib.sha256(frozen_bytes).hexdigest()

            if len(frozen_rows) != 1:
                raise DriftError("EXPORT_SIDE_DRIFT", f"导出后旁路行数应为 1，实为 {len(frozen_rows)}")
            disk_first_m2 = frozen_rows[0].get("paper_m2")
            if disk_first_m2 != paper_m2_1:
                raise DriftError(
                    "EXPORT_SIDE_DRIFT",
                    f"旁路首条 paper_m2={disk_first_m2} 与测算 {paper_m2_1} 对不上",
                )
            if disk_checksum != export_meta["sha256"]:
                raise DriftError(
                    "EXPORT_SIDE_DRIFT",
                    f"全文校验和对不上: 把手={export_meta['sha256']} 读盘={disk_checksum}",
                )
            if export_meta.get("rows") != 1 or export_meta.get("first_paper_m2") != paper_m2_1:
                raise DriftError(
                    "EXPORT_SIDE_DRIFT",
                    f"导出元数据与读盘不一致: meta={export_meta}",
                )
            print(f"[2] 已导出 {sidecar.name}  行数=1  paper_m2={disk_first_m2}  sha256={disk_checksum[:16]}…")

            # ---- 步骤 3：写入另一礼盒（方形礼盒 box_id=2），故意不导出 ----
            r2 = client.post("/api/estimate", json={"box_id": 2, "save": True})
            if r2.status_code != 200:
                raise DriftError("DB_SIDE_DRIFT", f"第二单写入失败 HTTP {r2.status_code}: {r2.text}")
            paper_m2_2 = r2.json()["paper_m2"]
            if paper_m2_2 == paper_m2_1:
                raise DriftError("DB_SIDE_DRIFT", "两单面积相同，无法证明旁路区分了新旧行")
            print(f"[3] 写入第二单 box_id=2  paper_m2={paper_m2_2}  （不触发导出）")

            # ---- 步骤 4：主库用纸档列表必须多一行 ----
            rr = client.get("/api/runs")
            if rr.status_code != 200:
                raise DriftError("DB_SIDE_DRIFT", f"主库用纸档列表读取失败 HTTP {rr.status_code}")
            db_items = rr.json()["items"]
            if len(db_items) != 2:
                raise DriftError(
                    "DB_SIDE_DRIFT",
                    f"主库用纸档应多一行（共 2 行），实为 {len(db_items)} 行",
                )
            print(f"[4] 主库用纸档列表 {len(db_items)} 行（新行已进主库）")

            # ---- 步骤 5：重读旁路文件，必须冻结在第一次导出 ----
            now_bytes, now_rows = _raw_snapshot()
            now_checksum = hashlib.sha256(now_bytes).hexdigest()
            if len(now_rows) != 1:
                raise DriftError(
                    "DB_SIDE_DRIFT",
                    f"未导出但旁路行数被主库拖着变为 {len(now_rows)}（应仍为 1）",
                )
            if now_rows[0].get("paper_m2") != paper_m2_1:
                raise DriftError(
                    "DB_SIDE_DRIFT",
                    f"未导出但旁路首条 paper_m2 变为 {now_rows[0].get('paper_m2')}（应仍为 {paper_m2_1}）",
                )
            if now_checksum != disk_checksum:
                raise DriftError(
                    "DB_SIDE_DRIFT",
                    f"未导出但旁路全文校验和漂移: {disk_checksum[:16]}… -> {now_checksum[:16]}…",
                )
            if now_bytes != frozen_bytes:
                raise DriftError("DB_SIDE_DRIFT", "未导出但旁路文件原始字节发生变化")
            print(f"[5] 旁路仍冻结: 行数=1  paper_m2={paper_m2_1}  sha256={now_checksum[:16]}…")

            # ---- 再导出一次：快照才追上主库（佐证导出是旁路唯一真相入口）----
            re2 = client.post("/api/paper-snapshot/export")
            meta2 = re2.json()
            _, rows2 = _raw_snapshot()
            if meta2.get("rows") != 2 or len(rows2) != 2:
                raise DriftError("EXPORT_SIDE_DRIFT", "再次导出后旁路应含 2 行")
            if [r["run_id"] for r in rows2] != [1, 2]:
                raise DriftError("EXPORT_SIDE_DRIFT", f"再次导出后行序异常: {[r['run_id'] for r in rows2]}")
            if rows2[0].get("paper_m2") != paper_m2_1 or rows2[1].get("paper_m2") != paper_m2_2:
                raise DriftError(
                    "EXPORT_SIDE_DRIFT",
                    f"再次导出后面积对不上: {[r.get('paper_m2') for r in rows2]}",
                )
            print(f"[6] 再次导出后旁路 {len(rows2)} 行，新行 paper_m2={paper_m2_2} 才进入快照")

        print("PASS: 旁路落盘与冻结核验通过（导出快照为旁路真相，主库新行不拖拽旁路）")
        keep_evidence = False
        return EXIT_OK
    except DriftError as exc:
        print(f"FAIL [{exc.side}] {exc}")
        return EXIT_DRIFT
    except Exception as exc:  # 基础设施类故障按非零退出，并保留现场目录
        print(f"FAIL [INFRA] {type(exc).__name__}: {exc}  现场数据目录: {_TMP_DATA}")
        return EXIT_DRIFT
    finally:
        # 通过时清理隔离目录；失败时保留现场以便排查（路径见上方输出前缀）。
        if not keep_evidence and os.path.isdir(_TMP_DATA):
            try:
                shutil.rmtree(_TMP_DATA)
            except OSError:
                pass


if __name__ == "__main__":
    sys.exit(main())
