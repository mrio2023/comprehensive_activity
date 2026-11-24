"""第 4 步 · 构造 Text2Cypher 测试集（英文图谱 medical_kg_en）。

做法：从图谱里挑关系最多的疾病，按模板生成中英文自然语言问题，
并执行「人工写的标准 Cypher」把标准答案一并存下来（执行准确率用它对比）。

输出：data/testset/text2cypher_testset.json
运行：python3 scripts/build_testset.py
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from kg_common import ROOT, TuGraphClient, log

OUT = ROOT / "data" / "testset" / "text2cypher_testset.json"


def main() -> int:
    c = TuGraphClient("medical_kg_en")
    rows = c.run("MATCH (d:Disease)-[r]->(x) RETURN d.name AS d, type(r) AS t, x.name AS x")

    bucket: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for r in rows:
        bucket[r["d"]][r["t"]].append(r["x"])

    # 每种关系挑 2 个「该关系下实体最多」的疾病，保证答案非空
    picked: dict[str, list[str]] = {}
    for rel in ("HAS_SYMPTOM", "REQUIRES_EXAM", "USES_DRUG", "BELONGS_TO"):
        cand = [(d, v[rel]) for d, v in bucket.items() if v.get(rel)]
        cand.sort(key=lambda kv: -len(kv[1]))
        picked[rel] = [d for d, _ in cand[:2]]

    templates = []
    for d in picked["HAS_SYMPTOM"]:
        templates.append((
            f"{d} 有哪些症状？",
            "MATCH (d:Disease)-[r:HAS_SYMPTOM]->(s:Symptom) WHERE d.name = $name "
            "RETURN s.name AS answer ORDER BY s.name",
            d,
        ))
    for d in picked["REQUIRES_EXAM"]:
        templates.append((
            f"得了 {d} 需要做哪些检查？",
            "MATCH (d:Disease)-[r:REQUIRES_EXAM]->(e:Examination) WHERE d.name = $name "
            "RETURN e.name AS answer ORDER BY e.name",
            d,
        ))
    for d in picked["USES_DRUG"]:
        templates.append((
            f"{d} 一般用什么药？",
            "MATCH (d:Disease)-[r:USES_DRUG]->(m:Drug) WHERE d.name = $name "
            "RETURN m.name AS answer ORDER BY m.name",
            d,
        ))
    for d in picked["BELONGS_TO"]:
        templates.append((
            f"{d} 应该去哪个科室看？",
            "MATCH (d:Disease)-[r:BELONGS_TO]->(p:Department) WHERE d.name = $name "
            "RETURN p.name AS answer ORDER BY p.name",
            d,
        ))

    # 统计类 + 反查类
    templates.append(("图谱里一共有多少个疾病节点？",
                      "MATCH (d:Disease) RETURN count(d) AS answer", None))
    templates.append(("图谱里一共有多少条关系？",
                      "MATCH ()-[r]->() RETURN count(r) AS answer", None))
    templates.append(("有哪些疾病会出现 itching 这个症状？",
                      "MATCH (d:Disease)-[r:HAS_SYMPTOM]->(s:Symptom) WHERE s.name = $name "
                      "RETURN d.name AS answer ORDER BY d.name", "itching"))

    testset = []
    for i, (q, cy, name) in enumerate(templates, 1):
        params = {"name": name} if name else {}
        gold = [r["answer"] for r in c.run(cy, params)]
        testset.append({
            "id": f"T{i:02d}",
            "question": q,
            "gold_cypher": cy,
            "params": params,
            "gold_answer": gold,
            "gold_size": len(gold),
        })
        log(f"  {i:02d}. {q}  -> 标准答案 {len(gold)} 条 {str(gold[:3])[:60]}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"graph": "medical_kg_en", "items": testset},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"\n测试集 {len(testset)} 条 -> {OUT}")
    c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
