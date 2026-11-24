"""第 2 步（2/3）· 导入课堂小样例：8 个节点 + 6 条关系（对应 PPT 验收口径）。

数据文件：
  data/demo/nodes.csv   label,id,name,description
  data/demo/edges.csv   src_label,src_id,relation,dst_label,dst_id,qa_id,evidence,scope

导入方式：标准 Cypher + 参数化 MERGE（幂等，可重复执行）。
运行：python3 scripts/import_demo.py
      python3 scripts/import_demo.py --reset   # 先清空目标图再导
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

from kg_common import ROOT, TuGraphClient, log, upsert_edges, upsert_vertices

DEMO_DIR = ROOT / "data" / "demo"

# 关系名 -> Cypher 关系类型（白名单，防止拼接注入）
REL_TYPES = {"HAS_SYMPTOM", "USES_DRUG", "BELONGS_TO", "REQUIRES_EXAM"}


def read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--graph", default=None)
    ap.add_argument("--reset", action="store_true", help="导入前清空目标图")
    args = ap.parse_args()

    nodes = read_csv(DEMO_DIR / "nodes.csv")
    edges = read_csv(DEMO_DIR / "edges.csv")

    with TuGraphClient(args.graph) as c:
        log("=" * 66)
        log(f" 导入课堂样例数据 —— 图 {c.graph}")
        log("=" * 66)
        log(f"待导入：节点 {len(nodes)} 个，关系 {len(edges)} 条")

        if args.reset:
            c.run("MATCH (n) DETACH DELETE n")
            log("已清空目标图")

        # ---- 节点：按标签分组写入 ----
        by_label: dict[str, list[dict]] = {}
        for n in nodes:
            by_label.setdefault(n["label"], []).append(n)

        for label, rows in by_label.items():
            upsert_vertices(c, label, rows)
            log(f"  [{label:12s}] 写入 {len(rows)} 个节点")

        # ---- 关系：按 (起点标签, 关系, 终点标签) 分组写入 ----
        by_rel: dict[tuple, list[dict]] = {}
        for e in edges:
            if e["relation"] not in REL_TYPES:
                raise ValueError(f"未知关系类型：{e['relation']}")
            by_rel.setdefault((e["src_label"], e["relation"], e["dst_label"]), []).append(e)

        for (src, rel, dst), rows in by_rel.items():
            upsert_edges(c, src, rel, dst, rows)
            log(f"  [{src} -{rel}-> {dst}] 写入 {len(rows)} 条关系")

        log(f"\n导入完成：节点 {c.count_nodes()} 个，关系 {c.count_edges()} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
