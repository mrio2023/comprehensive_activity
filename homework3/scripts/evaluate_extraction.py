"""第 2/4 步 · 抽取质量评价：与人工参考标注对比，算 P / R / F1。

对比对象（PPT 第 18、19 页的口径）：
  实体：rule_simple（简单子串）、rule_longest（最长匹配优先）、llm（大模型）
  关系：rule、llm
匹配方式：实体按 (规范化名称, 类型) 精确匹配；关系按 (头实体, 关系, 尾实体) 精确匹配。

输出：
  results/eval_ner.json / results/eval_ner.md
  results/eval_re.json / results/eval_re.md
  data/processed/evaluation_errors_ner.csv   （遗漏/误报明细，对应 PPT「打开 evaluation_errors.csv」）

运行：python3 scripts/evaluate_extraction.py
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

from kg_common import ROOT, log

PROCESSED = ROOT / "data" / "processed"
GOLD = ROOT / "data" / "annotations" / "gold_12.jsonl"
RESULTS = ROOT / "results"
DICT_PATH = ROOT / "data" / "dictionary" / "terms.json"


def norm(name: str, normalize: dict[str, str]) -> str:
    s = re.sub(r"\s+", " ", (name or "").strip().lower()).strip(" .,;:")
    return normalize.get(s, s)


def load_jsonl(p: Path) -> list[dict]:
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def prf(tp: int, fp: int, fn: int) -> dict:
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "precision": round(p, 4), "recall": round(r, 4), "f1": round(f, 4)}


def main() -> int:
    normalize = {k.lower(): v.lower() for k, v in
                 json.loads(DICT_PATH.read_text(encoding="utf-8")).get("normalize", {}).items()}
    gold = load_jsonl(GOLD)
    gold_ids = {g["qa_id"] for g in gold}
    log("=" * 66)
    log(f" 抽取质量评价 —— 人工参考标注 {len(gold)} 条")
    log("=" * 66)

    # ---------------- 实体 ----------------
    ner_summary, error_rows = {}, []
    for method in ("rule_simple", "rule_longest", "llm"):
        path = PROCESSED / f"ner_{method}.jsonl"
        if not path.exists():
            continue
        pred = {r["qa_id"]: r for r in load_jsonl(path) if r["qa_id"] in gold_ids}
        tp = fp = fn = 0
        for g in gold:
            gold_set = {(norm(e["text"], normalize), e["type"]) for e in g["entities"]}
            p_set = {(norm(e["text"], normalize), e["type"])
                     for e in pred.get(g["qa_id"], {}).get("entities", [])}
            tp += len(gold_set & p_set)
            fp += len(p_set - gold_set)
            fn += len(gold_set - p_set)
            for t in sorted(p_set - gold_set):
                error_rows.append([method, g["qa_id"], "FP", t[1], t[0]])
            for t in sorted(gold_set - p_set):
                error_rows.append([method, g["qa_id"], "FN", t[1], t[0]])
        ner_summary[method] = prf(tp, fp, fn)
        log(f"  {method:12s} P={ner_summary[method]['precision']:.2%} "
            f"R={ner_summary[method]['recall']:.2%} F1={ner_summary[method]['f1']:.2%}")

    # 分类型明细（用 llm 方法展示）
    typed = {}
    for g in gold:
        pred = {r["qa_id"]: r for r in load_jsonl(PROCESSED / "ner_llm.jsonl") if r["qa_id"] in gold_ids}
        break
    for t in ("Disease", "Symptom", "Drug", "Examination", "Department"):
        tp = fp = fn = 0
        for g in gold:
            gs = {(norm(e["text"], normalize)) for e in g["entities"] if e["type"] == t}
            ps = {(norm(e["text"], normalize))
                  for e in pred.get(g["qa_id"], {}).get("entities", []) if e["type"] == t}
            tp += len(gs & ps); fp += len(ps - gs); fn += len(gs - ps)
        typed[t] = prf(tp, fp, fn)

    # ---------------- 关系 ----------------
    re_summary = {}
    for method in ("rule", "llm"):
        path = PROCESSED / f"re_{method}.jsonl"
        if not path.exists():
            continue
        pred = {r["qa_id"]: r for r in load_jsonl(path) if r["qa_id"] in gold_ids}
        tp = fp = fn = 0
        for g in gold:
            gs = {(norm(r["head"], normalize), r["relation"], norm(r["tail"], normalize)) for r in g["relations"]}
            ps = {(norm(r["head"], normalize), r["relation"], norm(r["tail"], normalize))
                  for r in pred.get(g["qa_id"], {}).get("relations", [])}
            tp += len(gs & ps); fp += len(ps - gs); fn += len(gs - ps)
        re_summary[method] = prf(tp, fp, fn)
        log(f"  关系 {method:8s} P={re_summary[method]['precision']:.2%} "
            f"R={re_summary[method]['recall']:.2%} F1={re_summary[method]['f1']:.2%}")

    # ---------------- 落盘 ----------------
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "eval_ner.json").write_text(
        json.dumps({"gold_size": len(gold), "overall": ner_summary, "by_type_llm": typed},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    (RESULTS / "eval_re.json").write_text(
        json.dumps({"gold_size": len(gold), "overall": re_summary}, ensure_ascii=False, indent=2),
        encoding="utf-8")

    with (PROCESSED / "evaluation_errors_ner.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["method", "qa_id", "error_type", "entity_type", "entity"])
        w.writerows(error_rows)

    lines = ["| 方法 | Precision | Recall | F1 |", "|---|---|---|---|"]
    for m, v in ner_summary.items():
        lines.append(f"| {m} | {v['precision']:.2%} | {v['recall']:.2%} | {v['f1']:.2%} |")
    lines += ["", "| 实体类型（llm） | Precision | Recall | F1 |", "|---|---|---|---|"]
    for t, v in typed.items():
        lines.append(f"| {t} | {v['precision']:.2%} | {v['recall']:.2%} | {v['f1']:.2%} |")
    (RESULTS / "eval_ner.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    lines = ["| 方法 | Precision | Recall | F1 |", "|---|---|---|---|"]
    for m, v in re_summary.items():
        lines.append(f"| {m} | {v['precision']:.2%} | {v['recall']:.2%} | {v['f1']:.2%} |")
    (RESULTS / "eval_re.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    log(f"\n明细 -> {PROCESSED / 'evaluation_errors_ner.csv'}")
    log(f"结果 -> results/eval_ner.json, results/eval_re.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
