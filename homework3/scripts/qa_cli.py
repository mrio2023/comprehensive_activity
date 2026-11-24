"""第 3 步 · 交互式问答系统（命令行）。

对应 PPT 第 27 页的交互形态：输入问题 -> 生成只读 Cypher -> 执行 -> 展示结果与证据来源。

运行：
  python3 scripts/qa_cli.py                       # 默认图 medical_kg_en
  python3 scripts/qa_cli.py --graph medical_kg    # 课堂中文小样例图
  python3 scripts/qa_cli.py -q "scabies 用哪些药？"  # 单次提问（便于截图）
"""
from __future__ import annotations

import argparse

from text2cypher import Text2Cypher


def show(res: dict, limit_rows: int = 10) -> None:
    print(f"\n  生成 Cypher：{res['cypher']}")
    if res["error"]:
        print(f"  ⚠ {res['error']}")
        return
    rows = res["rows"]
    if not rows:
        print("  当前图谱无法回答（查询结果为空）。")
        return
    print(f"  查询到 {len(rows)} 条记录：")
    for i, r in enumerate(rows[:limit_rows], 1):
        cells = []
        for k, v in r.items():
            if k in ("evidence", "source", "qa_id"):
                continue
            cells.append(f"{v}")
        line = "    ".join(str(x) for x in cells)
        ev = r.get("evidence")
        src = r.get("source") or r.get("qa_id")
        tail = []
        if src:
            tail.append(f"来源：{src}")
        if ev:
            tail.append(f"证据：{str(ev)[:70]}")
        tail.append("审核状态：课堂参考，未经医学核验")
        print(f"  {i}. {line}    " + "  ".join(tail))
    if len(rows) > limit_rows:
        print(f"  …… 其余 {len(rows) - limit_rows} 条省略")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--graph", default="medical_kg_en")
    ap.add_argument("--mode", default="schema_aware",
                    choices=["baseline", "schema", "schema_aware"])
    ap.add_argument("-q", "--question", default=None)
    args = ap.parse_args()

    t2c = Text2Cypher(graph=args.graph, mode=args.mode)
    print("=" * 60)
    print(f" 医学知识图谱问答系统（图：{args.graph}，模式：{args.mode}）")
    print("=" * 60)

    if args.question:
        print(f"\n问题> {args.question}")
        show(t2c.ask(args.question))
        t2c.close()
        return 0

    print("请输入问题（输入 exit 退出，示例：scabies 用哪些药？）")
    while True:
        try:
            q = input("\n问题> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n问答结束。")
            break
        if not q:
            continue
        if q.lower() in ("exit", "quit"):
            print("问答结束。")
            break
        show(t2c.ask(q))
    t2c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
