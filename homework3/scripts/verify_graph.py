"""第 2 步（3/3）· 图谱自动验证（标准 Cypher）。

验收口径（PPT 第 8 页）：
  - 节点数 = 8，关系数 = 6
  - 查询「糖尿病」的症状应得到「多饮」「多尿」

运行：python3 scripts/verify_graph.py
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from kg_common import ROOT, TuGraphClient, get_config, log

EXPECT_NODES = 8
EXPECT_EDGES = 6


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--graph", default=None)
    ap.add_argument("--expect-nodes", type=int, default=EXPECT_NODES)
    ap.add_argument("--expect-edges", type=int, default=EXPECT_EDGES)
    ap.add_argument("--report", default="results/verify_demo.json")
    args = ap.parse_args()

    checks: list[tuple[str, bool, str]] = []
    graph_name = args.graph or get_config()["TGRAPH_GRAPH"]

    with TuGraphClient(args.graph) as c:
        log("=" * 66)
        log(f" 图谱验证 —— 图 {c.graph}")
        log("=" * 66)

        # 1) 规模
        n, e = c.count_nodes(), c.count_edges()
        log(f"[1] MATCH (n) RETURN count(n)          -> {n}（期望 {args.expect_nodes}）")
        log(f"    MATCH ()-[r]->() RETURN count(r)    -> {e}（期望 {args.expect_edges}）")
        checks.append(("节点数", n == args.expect_nodes, f"{n}/{args.expect_nodes}"))
        checks.append(("关系数", e == args.expect_edges, f"{e}/{args.expect_edges}"))

        # 2) 按标签统计
        log("\n[2] 各标签节点数：")
        rows = c.run(
            "MATCH (n) RETURN labels(n) AS labels, count(*) AS cnt ORDER BY cnt DESC"
        )
        for r in rows:
            lab = r["labels"][0] if isinstance(r["labels"], list) else r["labels"]
            log(f"    {lab:14s} {r['cnt']}")
        checks.append(("5 类节点", len(rows) == 5, f"{len(rows)} 类"))

        # 3) 关系类型统计
        log("\n[3] 各关系类型条数：")
        rows = c.run("MATCH ()-[r]->() RETURN type(r) AS t, count(*) AS cnt ORDER BY cnt DESC")
        for r in rows:
            log(f"    {r['t']:16s} {r['cnt']}")
        checks.append(("4 类关系", len(rows) == 4, f"{len(rows)} 类"))

        # 4) 关键属性完整性
        log("\n[4] 关键属性检查：")
        no_name = c.run("MATCH (n) WHERE n.name IS NULL OR n.name = '' RETURN count(n) AS c")[0]["c"]
        no_prop = c.run(
            "MATCH ()-[r]->() WHERE r.qa_id IS NULL OR r.evidence IS NULL RETURN count(r) AS c"
        )[0]["c"]
        log(f"    缺少 name 的节点：{no_name}；缺少 qa_id/evidence 的关系：{no_prop}")
        checks.append(("属性完整", no_name == 0 and no_prop == 0, f"缺 name={no_name}, 缺证据={no_prop}"))

        # 5) 业务查询：糖尿病 -> 症状
        log("\n[5] 业务查询：糖尿病有哪些症状？")
        cypher = (
            "MATCH (d:Disease)-[r:HAS_SYMPTOM]->(s:Symptom) "
            "WHERE d.name = '糖尿病' "
            "RETURN s.name AS symptom, r.evidence AS evidence"
        )
        log(f"    Cypher: {cypher}")
        rows = c.run(cypher)
        syms = [r["symptom"] for r in rows]
        for r in rows:
            log(f"    - {r['symptom']}    证据：{r['evidence'][:40]}…")
        ok = {"多饮", "多尿"}.issubset(set(syms))
        checks.append(("糖尿病 -> 多饮/多尿", ok, str(syms)))

        # 6) 业务查询：糖尿病 -> 检查 / 科室 / 药物
        log("\n[6] 关联查询（检查 / 科室 / 药物）：")
        for rel, tgt, q in (
            ("REQUIRES_EXAM", "Examination", "糖尿病需要做哪些检查？"),
            ("BELONGS_TO", "Department", "糖尿病属于哪个科室？"),
            ("USES_DRUG", "Drug", "糖尿病用什么药？"),
        ):
            rows = c.run(
                f"MATCH (d:Disease)-[r:{rel}]->(x:{tgt}) WHERE d.name = '糖尿病' "
                f"RETURN x.name AS name, r.qa_id AS source"
            )
            log(f"    {q} -> {[r['name'] for r in rows]}（来源 {[r['source'] for r in rows]}）")
            checks.append((f"糖尿病->{tgt}", len(rows) > 0, str([r["name"] for r in rows])))

    log("\n" + "-" * 66)
    passed = sum(1 for _, ok, _ in checks if ok)
    log(f"验证结果：{passed}/{len(checks)} 项通过")
    for name, ok, detail in checks:
        log(f"  [{'PASS' if ok else 'FAIL'}] {name:22s} {detail}")
    log("-" * 66)

    out = ROOT / args.report
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "graph": graph_name,
                "checks": [{"name": n, "pass": ok, "detail": d} for n, ok, d in checks],
                "passed": passed,
                "total": len(checks),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    log(f"结果已写入 {out}")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
