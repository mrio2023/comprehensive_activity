"""第 2 步 · 实体抽取（NER）：规则方法 vs 大模型方法。

两种规则实现（对应 PPT 讨论题「为什么最长匹配优先只让 F1 提高了一点？」）：
  --method rule_simple   词典子串匹配（不去重叠，包含/被包含的实体都会命中）
  --method rule_longest  最长匹配优先 + 词边界（解决实体位置重叠）
  --method llm           提示词驱动的大模型抽取（Schema 约束 + 禁止常识补充）

输出：
  data/processed/ner_{method}.jsonl
  每行 {qa_id, method, entities:[{text, type, surface, start, end}]}

运行：
  python3 scripts/extract_ner.py --method rule_simple
  python3 scripts/extract_ner.py --method rule_longest
  python3 scripts/extract_ner.py --method llm --limit 150
"""
from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

from kg_common import ROOT, LLMClient, log, parse_json_block

PROCESSED = ROOT / "data" / "processed"
DICT_PATH = ROOT / "data" / "dictionary" / "terms.json"
TYPES = ["Disease", "Symptom", "Drug", "Department", "Examination"]


# --------------------------------------------------------------------------
# 词典
# --------------------------------------------------------------------------
def load_dictionary() -> tuple[dict[str, list[str]], dict[str, str]]:
    data = json.loads(DICT_PATH.read_text(encoding="utf-8"))
    terms: dict[str, list[str]] = {}
    for t in TYPES:
        words = data.get(t, {}).get("terms", [])
        terms[t] = sorted({w.lower() for w in words}, key=len, reverse=True)
    normalize = {k.lower(): v.lower() for k, v in data.get("normalize", {}).items()}
    return terms, normalize


def load_samples(limit: int | None = None) -> list[dict]:
    rows = [
        json.loads(l)
        for l in (PROCESSED / "qa_subset.jsonl").read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]
    return rows[:limit] if limit else rows


# --------------------------------------------------------------------------
# 规则 NER
# --------------------------------------------------------------------------
def rule_extract(text: str, terms: dict[str, list[str]], normalize: dict[str, str],
                 longest: bool) -> list[dict]:
    low = text.lower()
    hits: list[dict] = []
    for etype, words in terms.items():
        for w in words:  # 已按长度倒序
            for m in re.finditer(r"(?<![a-z])" + re.escape(w) + r"(?![a-z])", low):
                hits.append(
                    {"surface": text[m.start() : m.end()], "text": w, "type": etype,
                     "start": m.start(), "end": m.end()}
                )
    if not longest:
        # 简单模式：保留全部命中（重叠也留着，用于对比）
        out = hits
    else:
        # 最长匹配优先：按起点排序，同起点取最长，且不与已选区间重叠
        hits.sort(key=lambda h: (h["start"], -(h["end"] - h["start"])))
        out, last_end = [], -1
        for h in hits:
            if h["start"] >= last_end:
                out.append(h)
                last_end = h["end"]
    # 去重 + 规范化
    seen, result = set(), []
    for h in out:
        canon = normalize.get(h["text"], h["text"])
        key = (canon, h["type"])
        if key in seen:
            continue
        seen.add(key)
        result.append({"text": canon, "type": h["type"], "surface": h["surface"],
                       "start": h["start"], "end": h["end"]})
    return result


def add_hints(row: dict, ents: list[dict]) -> list[dict]:
    """把数据自带的疾病主题、科室线索作为高置信实体补进去（来源可追溯）。"""
    have = {(e["text"], e["type"]) for e in ents}
    if row.get("disease_hint"):
        d = row["disease_hint"].lower()
        if (d, "Disease") not in have:
            ents.append({"text": d, "type": "Disease", "surface": row["disease_hint"],
                         "start": -1, "end": -1})
    if row.get("department_hint"):
        d = row["department_hint"].lower()
        if (d, "Department") not in have:
            ents.append({"text": d, "type": "Department", "surface": row["department_hint"],
                         "start": -1, "end": -1})
    return ents


# --------------------------------------------------------------------------
# 大模型 NER
# --------------------------------------------------------------------------
SYSTEM_PROMPT = """你是医学实体抽取助手。任务：从给定的英文医患问答中抽取医学实体。
实体类别只允许：Disease（疾病）、Symptom（症状）、Drug（药品）、Examination（检查）、Department（科室）。
规则：
1. 只抽取原文明确提及的实体，不得根据医学常识补充原文没有出现的实体；
2. 实体文本用原文出现的小写英文串，去除修饰语（如 "severe pain" 抽 "pain"）；
3. 同一实体只输出一次；
4. 只输出 JSON 数组，不要任何解释文字。格式：
[{"text":"tooth cavity","type":"Disease"}]"""


def llm_extract(client: LLMClient, text: str) -> list[dict]:
    user = f"文本：\n{text}\n\n请输出实体 JSON 数组。"
    raw = client.chat(SYSTEM_PROMPT, user, max_tokens=800)
    data = parse_json_block(raw)
    out = []
    if isinstance(data, list):
        for it in data:
            if not isinstance(it, dict):
                continue
            t, ty = str(it.get("text", "")).strip().lower(), str(it.get("type", "")).strip()
            if t and ty in TYPES:
                out.append({"text": t, "type": ty, "surface": t, "start": -1, "end": -1})
    elif isinstance(data, dict) and isinstance(data.get("entities"), list):
        for it in data["entities"]:
            t, ty = str(it.get("text", "")).strip().lower(), str(it.get("type", "")).strip()
            if t and ty in TYPES:
                out.append({"text": t, "type": ty, "surface": t, "start": -1, "end": -1})
    # 去重
    seen, uniq = set(), []
    for e in out:
        if (e["text"], e["type"]) in seen:
            continue
        seen.add((e["text"], e["type"]))
        uniq.append(e)
    return uniq


# --------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--method", default="rule_longest",
                    choices=["rule_simple", "rule_longest", "llm"])
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--sleep", type=float, default=0.0)
    args = ap.parse_args()

    samples = load_samples(args.limit or None)
    terms, normalize = load_dictionary()
    out_path = PROCESSED / f"ner_{args.method}.jsonl"

    if args.method.startswith("rule"):
        longest = args.method == "rule_longest"
        log(f"规则 NER（{'最长匹配优先' if longest else '简单子串匹配'}）—— 样本 {len(samples)} 条")
        log(f"词典规模：{ {k: len(v) for k, v in terms.items()} }")
        total = 0
        with out_path.open("w", encoding="utf-8") as f:
            for row in samples:
                ents = rule_extract(row["text"], terms, normalize, longest)
                ents = add_hints(row, ents)
                total += len(ents)
                f.write(json.dumps({"qa_id": row["qa_id"], "method": args.method,
                                    "entities": ents}, ensure_ascii=False) + "\n")
        log(f"完成：{len(samples)} 条，实体 {total} 个，平均 {total / max(1, len(samples)):.1f} 个/条")
        log(f"输出 -> {out_path}")
        return 0

    # LLM
    client = LLMClient()
    if not client.available():
        log("未配置 LLM_API_KEY（.env），无法运行大模型抽取")
        return 1
    log(f"大模型 NER（{client.model}）—— 样本 {len(samples)} 条")
    t0 = time.time()
    total = 0
    with out_path.open("w", encoding="utf-8") as f:
        for i, row in enumerate(samples, 1):
            try:
                ents = llm_extract(client, row["text"])
            except Exception as e:
                log(f"  [{i}/{len(samples)}] {row['qa_id']} 失败：{e}")
                ents = []
            ents = add_hints(row, ents)
            total += len(ents)
            f.write(json.dumps({"qa_id": row["qa_id"], "method": "llm",
                                "entities": ents}, ensure_ascii=False) + "\n")
            if i % 10 == 0 or i == len(samples):
                log(f"  {i}/{len(samples)} 条完成，累计实体 {total}，"
                    f"用时 {time.time() - t0:.0f}s")
            if args.sleep:
                time.sleep(args.sleep)
    log(f"完成：实体 {total} 个，平均 {total / max(1, len(samples)):.1f} 个/条")
    log(f"调用次数 {client.calls}，token 消耗 prompt={client.prompt_tokens} completion={client.completion_tokens}")
    log(f"输出 -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
