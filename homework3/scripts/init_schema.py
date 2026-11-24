"""第 2 步（1/3）· 初始化图 Schema：5 类节点 + 4 类关系。

节点统一属性：id（主键）、name、description
关系统一属性：qa_id（来源问答）、evidence（原文证据）、scope（适用范围 general / case_specific）

运行：python3 scripts/init_schema.py            # 默认图 medical_kg
      python3 scripts/init_schema.py --graph medical_kg_en
"""
from __future__ import annotations

import argparse

from kg_common import TuGraphClient, log

# 5 类节点
VERTEX_LABELS = ["Disease", "Symptom", "Drug", "Department", "Examination"]

# 4 类关系：名称 -> (起点标签, 终点标签, 语义)
EDGE_LABELS = {
    "HAS_SYMPTOM": ("Disease", "Symptom", "疾病表现出该症状"),
    "USES_DRUG": ("Disease", "Drug", "疾病使用该药品"),
    "BELONGS_TO": ("Disease", "Department", "疾病属于该科室"),
    "REQUIRES_EXAM": ("Disease", "Examination", "疾病需要该检查"),
}

# 字段参数形式：field_name, field_type, is_optional
# 主键 id 不可为空；其余字段声明为可选，便于 MERGE 后再 SET（TuGraph 的 MERGE 会先按主键建点）
VERTEX_FIELDS = "'id','STRING',false,'name','STRING',true,'description','STRING',true"
EDGE_FIELDS = "'qa_id','STRING',true,'evidence','STRING',true,'scope','STRING',true"

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--graph", default=None)
    args = ap.parse_args()

    with TuGraphClient(args.graph) as c:
        log("=" * 66)
        log(f" 初始化 Schema —— 图 {c.graph}")
        log("=" * 66)

        if c.ensure_graph("实验3 医疗知识图谱"):
            log(f"[建图] {c.graph} 不存在，已创建空图")

        exist_v = {d["label"] for d in c.labels()}
        exist_e = {d["label"] for d in c.edge_labels()}

        for lab in VERTEX_LABELS:
            if lab in exist_v:
                log(f"[跳过] 点标签 {lab} 已存在")
                continue
            c.run(f"CALL db.createVertexLabel('{lab}','id',{VERTEX_FIELDS})")
            log(f"[创建] 点标签 {lab}（主键 id）")

        for lab, (src, dst, desc) in EDGE_LABELS.items():
            if lab in exist_e:
                log(f"[跳过] 边标签 {lab} 已存在")
                continue
            # 注意：TuGraph 的 db.createEdgeLabel 第二个参数是 JSON 字符串
            # 形如 '[["Disease","Symptom"]]'，不能直接传 list
            pairs = f'[["{src}","{dst}"]]'
            c.run(f"CALL db.createEdgeLabel('{lab}','{pairs}',{EDGE_FIELDS})")
            log(f"[创建] 边标签 {lab}: {src} -> {dst}（{desc}）")

        log("\n当前 Schema：")
        log(f"  点标签 {len(c.labels())} 个 : {[d['label'] for d in c.labels()]}")
        log(f"  边标签 {len(c.edge_labels())} 个 : {[d['label'] for d in c.edge_labels()]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
