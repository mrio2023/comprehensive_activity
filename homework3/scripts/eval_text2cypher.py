"""第 4 步 · Text2Cypher 基线对比与消融实验。

三档配置（同一批问题、同一模型，仅 Prompt 信息不同）：
  baseline      不给 Schema、不给示例
  schema        给 Schema 与规则，不给示例
  schema_aware  给 Schema + 规则 + few-shot 示例（默认配置）

指标：
  合法率     生成的 Cypher 能被 TuGraph 执行（无语法/语义错误）
  执行准确率 查询结果集合与标准答案完全一致
  平均延迟   生成 + 执行耗时
  安全拦截   被只读检查拦下的条数

输出：results/text2cypher_eval.json / results/text2cypher_eval.md
运行：python3 scripts/eval_text2cypher.py --modes baseline schema schema_aware
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from kg_common import ROOT, log
from text2cypher import Text2Cypher

TESTSET = ROOT / "data" / "testset" / "text2cypher_testset.json"
RESULTS = ROOT / "results"


def norm_answer(v):
    if isinstance(v, (int, float)):
        return float(v)
    return str(v).strip().lower()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--modes", nargs="+", default=["baseline", "schema", "schema_aware"])
    ap.add_argument("--graph", default="medical_kg_en")
    args = ap.parse_args()

    items = json.loads(TESTSET.read_text(encoding="utf-8"))["items"]
    log(f"测试集 {len(items)} 条；配置：{args.modes}")

    summary, details = {}, []
    for mode in args.modes:
        t2c = Text2Cypher(graph=args.graph, mode=mode)
        ok_cnt = correct = blocked = 0
        lat = 0.0
        log(f"\n=== 配置 {mode} ===")
        for it in items:
            res = t2c.ask(it["question"])
            valid = bool(res["ok"])
            ok_cnt += valid
            lat += res.get("latency", 0)
            if res["error"].startswith("安全检查拦截"):
                blocked += 1
            got = []
            if valid:
                first_key = list(res["rows"][0].keys())[0] if res["rows"] else None
                got = [norm_answer(r[first_key]) for r in res["rows"] if first_key]
            gold = sorted(norm_answer(g) for g in it["gold_answer"])
            hit = sorted(got) == gold
            correct += hit
            log(f"  {it['id']} {'✓' if hit else '✗'} 合法={valid} "
                f"预测 {len(got)} 条 / 标准 {len(gold)} 条")
            if not hit:
                log(f"      cypher: {res['cypher'][:140]}")
                if res["error"]:
                    log(f"      error : {res['error'][:140]}")
            details.append({"mode": mode, "id": it["id"], "question": it["question"],
                            "cypher": res["cypher"], "valid": valid, "correct": hit,
                            "pred_size": len(got), "gold_size": len(gold),
                            "error": res["error"]})
        summary[mode] = {
            "n": len(items),
            "valid_rate": round(ok_cnt / len(items), 4),
            "exec_accuracy": round(correct / len(items), 4),
            "avg_latency_s": round(lat / len(items), 2),
            "blocked": blocked,
        }
        log(f"  -> 合法率 {ok_cnt}/{len(items)}，执行准确率 {correct}/{len(items)}，"
            f"平均延迟 {lat / len(items):.1f}s")
        t2c.close()

    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "text2cypher_eval.json").write_text(
        json.dumps({"summary": summary, "details": details}, ensure_ascii=False, indent=2),
        encoding="utf-8")

    md = ["| 配置 | 合法率 | 执行准确率 | 平均延迟(s) | 安全拦截 |", "|---|---|---|---|---|"]
    name_map = {"baseline": "baseline（无 Schema）", "schema": "Schema 感知（无示例）",
                "schema_aware": "Schema 感知 + few-shot"}
    for m, v in summary.items():
        md.append(f"| {name_map.get(m, m)} | {v['valid_rate']:.0%} | {v['exec_accuracy']:.0%} | "
                  f"{v['avg_latency_s']} | {v['blocked']} |")
    (RESULTS / "text2cypher_eval.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    log(f"\n结果 -> results/text2cypher_eval.json / .md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
