"""第 2 步 · 关系抽取（RE）：规则方法 vs 大模型方法。

规则方法（对应 PPT 第 22 页）：
  在句子内部用「线索词 + 实体共现」判定关系，必须保留原句作为证据，
  并把「医生建议做的检查」标为 case_specific，避免把个案建议当成普遍结论。

大模型方法（对应 PPT 第 23 页）：
  给出已识别实体 + 允许的关系白名单 + 证据要求，由模型判断原文是否支持该关系。

输出：data/processed/re_{method}.jsonl
  {qa_id, relations:[{head, head_type, relation, tail, tail_type, evidence, scope}]}

运行：
  python3 scripts/extract_re.py --method rule
  python3 scripts/extract_re.py --method llm --limit 50
"""
from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

from kg_common import ROOT, LLMClient, log, parse_json_block

PROCESSED = ROOT / "data" / "processed"

RELATIONS = {
    "HAS_SYMPTOM": ("Disease", "Symptom"),
    "USES_DRUG": ("Disease", "Drug"),
    "BELONGS_TO": ("Disease", "Department"),
    "REQUIRES_EXAM": ("Disease", "Examination"),
}

# 线索词：出现这些词才认为句子在表达对应关系
CUES = {
    "HAS_SYMPTOM": r"\b(symptom|symptoms|complain|complains|complaining|suffering|suffer from|"
                   r"present with|presenting|associated with|due to|causes|caused by|leads to|"
                   r"experiencing|feel|feels|feeling|history of)\b",
    "USES_DRUG": r"\b(take|takes|taking|tablet|tablets|capsule|medicine|medication|drug|"
                 r"prescribed|prescribe|dose|dosage|mg|ointment|cream|syrup|injection|supplement)\b",
    "REQUIRES_EXAM": r"\b(test|tests|testing|scan|scans|x-ray|investigation|investigations|"
                     r"check|checkup|examination|ultrasound|mri|ct|biopsy|advised|suggest|"
                     r"recommend|undergo|get done|get tested)\b",
    "BELONGS_TO": r"\b(consult|consultation|visit|see a|specialist|surgeon|doctor|physician|"
                  r"department|refer|referred)\b",
}
CASE_CUE = r"\b(advise|advised|suggest|suggested|recommend|recommended|you (?:can|should|need))\b"


def sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", text)
    return [p.strip() for p in parts if len(p.strip()) > 15]


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


# --------------------------------------------------------------------------
# 规则 RE
# --------------------------------------------------------------------------
def rule_re(text: str, ents: list[dict], disease_default: str | None) -> list[dict]:
    rels, seen = [], set()
    for sent in sentences(text):
        low = sent.lower()
        here = [e for e in ents if e["text"] and e["text"] in low]
        if not here:
            continue
        diseases = [e for e in here if e["type"] == "Disease"]
        head = diseases[0]["text"] if diseases else (disease_default or "")
        if not head:
            continue
        for e in here:
            for rel, (src_t, dst_t) in RELATIONS.items():
                if e["type"] != dst_t:
                    continue
                if not re.search(CUES[rel], low):
                    continue
                if e["text"] == head:  # 自环无意义
                    continue
                key = (head, rel, e["text"])
                if key in seen:
                    continue
                seen.add(key)
                scope = "case_specific" if re.search(CASE_CUE, low) else "general"
                rels.append(
                    {
                        "head": head, "head_type": "Disease", "relation": rel,
                        "tail": e["text"], "tail_type": e["type"],
                        "evidence": sent[:300], "scope": scope,
                    }
                )
    return rels


# --------------------------------------------------------------------------
# 大模型 RE
# --------------------------------------------------------------------------
SYSTEM_PROMPT = """你是医学关系抽取助手。给定一段英文医患问答和已识别的实体，判断实体之间是否存在下列关系：
- HAS_SYMPTOM: Disease -> Symptom（疾病表现出该症状）
- USES_DRUG: Disease -> Drug（疾病使用该药品）
- BELONGS_TO: Disease -> Department（疾病对应科室）
- REQUIRES_EXAM: Disease -> Examination（疾病需要的检查）
规则：
1. 只能使用上述 4 种关系，起点必须是 Disease，终点类型必须与关系匹配；
2. 必须有原文句子作为证据，禁止仅凭共现或医学常识建立关系；
3. 若是「医生建议/你应做」这类个案建议，scope 标为 case_specific，否则 general；
4. 只输出 JSON 数组，不要解释。格式：
[{"head":"tooth cavity","relation":"REQUIRES_EXAM","tail":"x-ray","evidence":"原文句子","scope":"general"}]"""


def llm_re(client: LLMClient, text: str, ents: list[dict]) -> list[dict]:
    ent_str = ", ".join(f"{e['text']}({e['type']})" for e in ents)
    user = f"实体：{ent_str}\n\n文本：\n{text}\n\n请输出关系三元组 JSON 数组。"
    raw = client.chat(SYSTEM_PROMPT, user, max_tokens=1200)
    data = parse_json_block(raw)
    if not isinstance(data, list):
        return []
    out, seen = [], set()
    for it in data:
        if not isinstance(it, dict):
            continue
        rel = str(it.get("relation", "")).strip()
        head = str(it.get("head", "")).strip().lower()
        tail = str(it.get("tail", "")).strip().lower()
        if rel not in RELATIONS or not head or not tail:
            continue
        if head == tail:
            continue
        key = (head, rel, tail)
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "head": head, "head_type": "Disease", "relation": rel,
                "tail": tail, "tail_type": RELATIONS[rel][1],
                "evidence": str(it.get("evidence", ""))[:300],
                "scope": "case_specific" if "case_specific" in str(it.get("scope", "")) else "general",
            }
        )
    return out


# --------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--method", default="rule", choices=["rule", "llm"])
    ap.add_argument("--ner", default=None, help="使用的 NER 结果文件（默认按 method 选择）")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--sleep", type=float, default=0.0)
    args = ap.parse_args()

    ner_path = PROCESSED / (args.ner or ("ner_rule_longest.jsonl" if args.method == "rule" else "ner_llm.jsonl"))
    ner_rows = load_jsonl(ner_path)
    samples = {
        json.loads(l)["qa_id"]: json.loads(l)
        for l in (PROCESSED / "qa_subset.jsonl").read_text(encoding="utf-8").splitlines()
        if l.strip()
    }
    if args.limit:
        ner_rows = ner_rows[: args.limit]

    out_path = PROCESSED / f"re_{args.method}.jsonl"
    log(f"关系抽取（{args.method}）—— NER 来源 {ner_path.name}，样本 {len(ner_rows)} 条")

    client = None
    if args.method == "llm":
        client = LLMClient()
        if not client.available():
            log("未配置 LLM_API_KEY")
            return 1

    t0 = time.time()
    total = 0
    with out_path.open("w", encoding="utf-8") as f:
        for i, row in enumerate(ner_rows, 1):
            qa = samples.get(row["qa_id"], {})
            text = qa.get("text", "")
            if args.method == "rule":
                rels = rule_re(text, row["entities"], (qa.get("disease_hint") or "").lower() or None)
            else:
                try:
                    rels = llm_re(client, text, row["entities"])
                except Exception as e:
                    log(f"  {row['qa_id']} 失败：{e}")
                    rels = []
            total += len(rels)
            f.write(json.dumps({"qa_id": row["qa_id"], "relations": rels}, ensure_ascii=False) + "\n")
            if args.method == "llm" and (i % 10 == 0 or i == len(ner_rows)):
                log(f"  {i}/{len(ner_rows)}，累计关系 {total}，用时 {time.time() - t0:.0f}s")
            if args.sleep:
                time.sleep(args.sleep)

    log(f"完成：关系 {total} 条，平均 {total / max(1, len(ner_rows)):.1f} 条/条")
    if client:
        log(f"调用 {client.calls} 次，token prompt={client.prompt_tokens} completion={client.completion_tokens}")
    log(f"输出 -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
