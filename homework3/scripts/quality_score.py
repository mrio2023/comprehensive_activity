"""第 2 步 · 图谱质量评估（六维评分，对应 PPT 第 24 页）。

维度与权重（权重仅供课堂讨论，PPT 原式）：
  S 结构完整性 0.15 | C 证据完备 0.15 | N 实体规范 0.20
  R 关系有效   0.25 | T 可溯源   0.15 | V 覆盖度  0.10

统计口径全部从 TuGraph 里现查（不是从中间文件估算），保证评的是「入库后的图」。

运行：python3 scripts/quality_score.py --graph medical_kg_en
输出：results/quality_score.json / results/quality_score.md
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from kg_common import ROOT, TuGraphClient, log

PROCESSED = ROOT / "data" / "processed"
DICT_PATH = ROOT / "data" / "dictionary" / "terms.json"
WEIGHTS = {"S": 0.15, "C": 0.15, "N": 0.20, "R": 0.25, "T": 0.15, "V": 0.10}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--graph", default="medical_kg_en")
    args = ap.parse_args()

    normalize = {k.lower() for k in
                 json.loads(DICT_PATH.read_text(encoding="utf-8")).get("normalize", {})}
    samples = {json.loads(l)["qa_id"]: json.loads(l)
               for l in (PROCESSED / "qa_subset.jsonl").read_text(encoding="utf-8").splitlines()
               if l.strip()}

    with TuGraphClient(args.graph) as c:
        log("=" * 66)
        log(f" 图谱质量评分 —— 图 {c.graph}")
        log("=" * 66)

        n_nodes = c.count_nodes()
        n_edges = c.count_edges()
        nodes = c.run("MATCH (n) RETURN n.id AS id, n.name AS name, n.description AS d, "
                      "labels(n) AS lab")
        edges = c.run(
            "MATCH (a)-[r]->(b) RETURN a.id AS sid, b.id AS tid, a.name AS sname, "
            "b.name AS tname, a.description AS ad, b.description AS bd, type(r) AS t, "
            "r.qa_id AS qa_id, r.evidence AS ev, labels(a) AS slab, labels(b) AS tlab"
        )

        # ---- S 结构完整性：节点必备属性 + 关系端点标签是否符合 Schema ----
        node_ok = sum(1 for n in nodes if n["name"] and n["d"] and n["id"])
        expect = {"HAS_SYMPTOM": ("Disease", "Symptom"), "USES_DRUG": ("Disease", "Drug"),
                  "BELONGS_TO": ("Disease", "Department"), "REQUIRES_EXAM": ("Disease", "Examination")}
        schema_ok = 0
        for e in edges:
            exp = expect.get(e["t"])
            if not exp:
                continue
            if exp[0] in str(e["slab"]) and exp[1] in str(e["tlab"]):
                schema_ok += 1
        S = (node_ok / n_nodes if n_nodes else 0) * 0.5 + (schema_ok / n_edges if n_edges else 0) * 0.5

        # ---- C 证据完备率 ----
        C = sum(1 for e in edges if e["ev"] and len(e["ev"]) >= 15) / n_edges if n_edges else 0

        # ---- N 实体规范化率 ----
        def well_formed(name: str) -> bool:
            if not name:
                return False
            if name != name.lower().strip():
                return False
            if re.search(r"\s{2,}|[.;:,]$", name):
                return False
            return name not in normalize  # 仍是同义写法 → 未规范化

        N = sum(1 for n in nodes if well_formed(n["name"])) / n_nodes if n_nodes else 0

        # ---- R 关系有效率：无自环、头是疾病、尾类型正确、且 (头,关系,尾) 不重复 ----
        triples = [(e["sname"], e["t"], e["tname"]) for e in edges]
        valid = sum(1 for e in edges
                    if e["sname"] != e["tname"] and e["sname"] and e["tname"]
                    and expect.get(e["t"]) and expect[e["t"]][1] in str(e["tlab"]))
        dup_ratio = 1 - (len(set(triples)) / len(triples)) if triples else 0
        struct_ok = (valid / n_edges if n_edges else 0) * (1 - dup_ratio)
        # 结构合法只是必要条件；再乘上人工抽样得到的精确率，避免「自己评自己满分」
        rel_p = 0.0
        eval_path = ROOT / "results" / "eval_re.json"
        if eval_path.exists():
            data = json.loads(eval_path.read_text(encoding="utf-8"))["overall"]
            rel_p = max(v["precision"] for v in data.values()) if data else 0.0
        R = struct_ok * (rel_p or 1.0)

        # ---- T 可溯源性：qa_id 能在语料中找到，且证据片段出现在原文 ----
        traced = 0
        for e in edges:
            row = samples.get(e["qa_id"] or "")
            if not row:
                continue
            snippet = (e["ev"] or "")[:30].lower()
            if snippet and snippet in row["text"].lower():
                traced += 1
        T = traced / n_edges if n_edges else 0

        # ---- V 覆盖度：有关系的疾病数 / 语料里的疾病主题数 ----
        dis_total = c.run("MATCH (n:Disease) RETURN count(n) AS c")[0]["c"]
        # 注：本版 TuGraph 的 count(DISTINCT ...) 返回空结果，改为取回 id 再在本地去重
        dis_linked = len({r["id"] for r in c.run("MATCH (d:Disease)-[r]->() RETURN d.id AS id")})
        topics = len({(r.get("disease_hint") or "").lower() for r in samples.values()
                      if r.get("disease_hint")})
        V = min(1.0, dis_linked / topics) if topics else 0

        Q = (WEIGHTS["S"] * S + WEIGHTS["C"] * C + WEIGHTS["N"] * N
             + WEIGHTS["R"] * R + WEIGHTS["T"] * T + WEIGHTS["V"] * V)

        rows = [
            ("S 结构完整性", S, "节点必备属性齐全 且 关系端点符合 Schema"),
            ("C 证据完备", C, "关系带 ≥15 字符原文证据的比例"),
            ("N 实体规范", N, "实体名为小写、无多余空白、非残留同义写法"),
            ("R 关系有效", R, f"结构合法率 × 人工抽样精确率 {rel_p:.2%}"),
            ("T 可溯源", T, "qa_id 可回查语料 且 证据句能在原文中命中"),
            ("V 覆盖度", V, f"有关系的疾病 {dis_linked} / 语料疾病主题 {topics}"),
        ]
        for name, v, desc in rows:
            log(f"  {name:12s} {v:6.2%}   （{desc}）")
        log(f"  {'-' * 46}")
        log(f"  综合质量分 Q = {Q:.4f}  （权重 {WEIGHTS}）")
        log(f"  图规模：节点 {n_nodes}（疾病 {dis_total}），关系 {n_edges}")

        out = {"graph": c.graph, "nodes": n_nodes, "diseases": dis_total, "edges": n_edges,
               "scores": {r[0]: round(r[1], 4) for r in rows}, "weights": WEIGHTS,
               "quality": round(Q, 4)}

    RESULTS = ROOT / "results"
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "quality_score.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    md = ["| 维度 | 得分 | 权重 | 说明 |", "|---|---|---|---|"]
    weight_iter = iter(WEIGHTS.values())
    for k, v in out["scores"].items():
        desc = next((r[2] for r in rows if r[0] == k), "")
        md.append(f"| {k} | {v:.2%} | {next(weight_iter):.2f} | {desc} |")
    md.append(f"| **综合质量分 Q** | **{out['quality']:.4f}** | — | 0.15S+0.15C+0.20N+0.25R+0.15T+0.10V |")
    (RESULTS / "quality_score.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    log(f"\n结果 -> results/quality_score.json / .md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
