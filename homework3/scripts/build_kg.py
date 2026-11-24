"""第 2 步 · 知识融合与图谱构建（抽取结果 -> TuGraph）。

流程：
  1. 读取规则方法与大模型方法的两份抽取结果（实体 + 关系）
  2. 实体规范化：小写化、同义词归一（terms.json 的 normalize）、去空白、过滤噪声
  3. 关系融合：同一 (head, relation, tail) 去重，保留首条证据，统计支持次数
  4. 写入 TuGraph：5 类节点 + 4 类关系（参数化 MERGE，幂等）

运行：
  python3 scripts/build_kg.py --reset
输出：
  data/processed/kg_nodes.csv / kg_edges.csv   （导入快照，便于人工核对）
  results/build_kg_stats.json
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from pathlib import Path

from kg_common import ROOT, TuGraphClient, log, upsert_edges, upsert_vertices

PROCESSED = ROOT / "data" / "processed"
DICT_PATH = ROOT / "data" / "dictionary" / "terms.json"

PREFIX = {"Disease": "DIS", "Symptom": "SYM", "Drug": "DRU", "Department": "DEP", "Examination": "EXA"}

# 噪声实体（过于笼统，进图谱只会污染）
STOP_ENTITIES = {
    "doctor", "patient", "medicine", "tablet", "test", "pain killer", "treatment",
    "surgery", "hospital", "clinic", "consultation", "advice", "problem", "issue",
    "infection", "disease", "disorder", "condition", "symptom", "cause", "report",
}

REL_TYPES = {"HAS_SYMPTOM", "USES_DRUG", "BELONGS_TO", "REQUIRES_EXAM"}


def load_jsonl(name: str) -> list[dict]:
    p = PROCESSED / name
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def norm_name(name: str, normalize: dict[str, str]) -> str:
    s = re.sub(r"\s+", " ", (name or "").strip().lower())
    s = re.sub(r"^(the|a|an)\s+", "", s)
    s = s.strip(" .,;:")
    return normalize.get(s, s)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--graph", default=None)
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--min-name-len", type=int, default=3)
    args = ap.parse_args()

    normalize = {k.lower(): v.lower() for k, v in
                 json.loads(DICT_PATH.read_text(encoding="utf-8")).get("normalize", {}).items()}

    ner_files = ["ner_rule_longest.jsonl", "ner_llm.jsonl"]
    re_files = ["re_rule.jsonl", "re_llm.jsonl"]
    ner_rows = [(f, r) for f in ner_files for r in load_jsonl(f)]
    re_rows = [(f, r) for f in re_files for r in load_jsonl(f)]

    log("=" * 66)
    log(" 知识融合与图谱构建")
    log("=" * 66)
    log(f" 实体来源：{ {f: sum(1 for x, _ in ner_rows if x == f) for f in ner_files} }")
    log(f" 关系来源：{ {f: sum(1 for x, _ in re_rows if x == f) for f in re_files} }")

    # ---------------- 1. 实体 ----------------
    nodes: dict[tuple[str, str], dict] = {}
    raw_ent = 0
    for src, row in ner_rows:
        for e in row["entities"]:
            raw_ent += 1
            name = norm_name(e["text"], normalize)
            etype = e["type"]
            if etype not in PREFIX:
                continue
            if len(name) < args.min_name_len or name in STOP_ENTITIES or not re.search(r"[a-z]", name):
                continue
            key = (etype, name)
            if key not in nodes:
                nodes[key] = {
                    "label": etype,
                    "id": f"{PREFIX[etype]}-{len(nodes) + 1:05d}",
                    "name": name,
                    "description": f"由 {src.replace('ner_', '').replace('.jsonl', '')} 抽取",
                    "sources": {src},
                }
            else:
                nodes[key]["sources"].add(src)

    log(f"\n[1] 实体：原始 {raw_ent} 个 -> 规范化去重后 {len(nodes)} 个")
    by_type = Counter(k[0] for k in nodes)
    log(f"    类型分布：{dict(by_type)}")

    # ---------------- 2. 关系 ----------------
    edges: dict[tuple[str, str, str], dict] = {}
    raw_rel = 0
    dropped = Counter()
    for src, row in re_rows:
        for r in row["relations"]:
            raw_rel += 1
            rel = r["relation"]
            if rel not in REL_TYPES:
                dropped["未知关系"] += 1
                continue
            head = norm_name(r["head"], normalize)
            tail = norm_name(r["tail"], normalize)
            if head == tail:
                dropped["自环"] += 1
                continue
            hk, tk = ("Disease", head), (r["tail_type"], tail)
            if hk not in nodes:
                dropped["头部实体未入库"] += 1
                continue
            if tk not in nodes:
                dropped["尾部实体未入库"] += 1
                continue
            key = (head, rel, tail)
            if key in edges:
                edges[key]["support"] += 1
                edges[key]["sources"].add(src)
                continue
            edges[key] = {
                "head": head, "relation": rel, "tail": tail,
                "head_id": nodes[hk]["id"], "tail_id": nodes[tk]["id"],
                "src_label": "Disease", "dst_label": r["tail_type"],
                "qa_id": row["qa_id"],
                "evidence": (r.get("evidence") or "")[:300],
                "scope": r.get("scope", "general"),
                "support": 1,
                "sources": {src},
            }

    log(f"\n[2] 关系：原始 {raw_rel} 条 -> 融合去重后 {len(edges)} 条")
    log(f"    丢弃明细：{dict(dropped)}")
    log(f"    关系分布：{dict(Counter(v['relation'] for v in edges.values()))}")
    log(f"    case_specific 占比：{sum(1 for v in edges.values() if v['scope'] == 'case_specific')}/{len(edges)}")

    # ---------------- 3. 落盘快照 ----------------
    nodes_csv = PROCESSED / "kg_nodes.csv"
    edges_csv = PROCESSED / "kg_edges.csv"
    with nodes_csv.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["label", "id", "name", "description"])
        for v in nodes.values():
            w.writerow([v["label"], v["id"], v["name"], v["description"]])
    with edges_csv.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["src_label", "src_id", "relation", "dst_label", "dst_id", "qa_id", "evidence", "scope"])
        for v in edges.values():
            w.writerow([v["src_label"], v["head_id"], v["relation"], v["dst_label"], v["tail_id"],
                        v["qa_id"], v["evidence"], v["scope"]])
    log(f"\n[3] 快照：{nodes_csv.name} / {edges_csv.name}")

    # ---------------- 4. 写入 TuGraph ----------------
    with TuGraphClient(args.graph or "medical_kg_en") as c:
        if args.reset:
            c.run("MATCH (n) DETACH DELETE n")
            log("    已清空目标图")
        grouped: dict[str, list[dict]] = {}
        for v in nodes.values():
            grouped.setdefault(v["label"], []).append(v)
        for label, rows in grouped.items():
            upsert_vertices(c, label, rows)
            log(f"    写入 {label:12s} {len(rows):4d} 个节点")
        by_rel: dict[tuple, list[dict]] = {}
        for v in edges.values():
            by_rel.setdefault((v["src_label"], v["relation"], v["dst_label"]), []).append(
                {"src_id": v["head_id"], "dst_id": v["tail_id"], "qa_id": v["qa_id"],
                 "evidence": v["evidence"], "scope": v["scope"]}
            )
        for (s, rel, d), rows in by_rel.items():
            upsert_edges(c, s, rel, d, rows)
            log(f"    写入 {s}-{rel}->{d} {len(rows)} 条")
        log(f"\n[4] 图 {c.graph} 当前规模：节点 {c.count_nodes()}，关系 {c.count_edges()}")

        stats = {
            "graph": c.graph,
            "raw_entities": raw_ent, "fused_entities": len(nodes),
            "raw_relations": raw_rel, "fused_relations": len(edges),
            "entity_type_dist": dict(by_type),
            "relation_type_dist": dict(Counter(v["relation"] for v in edges.values())),
            "case_specific": sum(1 for v in edges.values() if v["scope"] == "case_specific"),
            "dropped": dict(dropped),
            "sources": {"ner": ner_files, "re": re_files},
        }
    out = ROOT / "results" / "build_kg_stats.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"统计 -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
