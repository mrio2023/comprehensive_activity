"""第 2 步前置 · 医疗问答数据预处理。

输入（需自行解压，见 README）:
  data/raw/Medical-Dialogue-Dataset-English/*.txt
    - healthcaremagic_dialogue_1..4.txt：记录按 "id=N" 分段，字段为
      id / URL / Description（问题）/ Dialogue（Patient:… Doctor:…）
    - icliniq_dialogue.txt：记录首尾相接（无换行），格式相同

输出:
  data/processed/qa_subset.jsonl   清洗后的问答样本（默认抽样 300 条）
  data/processed/preprocess_stats.json

每条样本字段：
  qa_id, source, url, topic_slug, department_hint, question, patient_text,
  doctor_text, text（= question + patient_text + doctor_text，供抽取使用）

运行：python3 scripts/preprocess_data.py --limit 300
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
from collections import Counter
from pathlib import Path

from kg_common import ROOT, log

RAW_DIR = ROOT / "data" / "raw" / "Medical-Dialogue-Dataset-English"
OUT_DIR = ROOT / "data" / "processed"


# --------------------------------------------------------------------------
# 解析
# --------------------------------------------------------------------------
def split_records(text: str) -> list[str]:
    """按 'id=N' 切分记录（两种数据文件都是这个分段方式）。"""
    parts = re.split(r"(?=\bid=\d+)", text)
    return [p for p in parts if p.strip()]


def parse_record(chunk: str, source: str) -> dict | None:
    chunk = chunk.replace("\r", "")
    m_id = re.match(r"id=(\d+)", chunk)
    if not m_id:
        return None
    qa_id = f"{source[:2].upper()}-{int(m_id.group(1)):05d}"

    i_desc = chunk.find("Description")
    i_dlg = chunk.find("Dialogue")
    if i_desc == -1 or i_dlg == -1 or i_dlg <= i_desc:
        return None

    url = chunk[m_id.end() : i_desc].strip()
    question = chunk[i_desc + len("Description") : i_dlg].strip()
    question = re.sub(r"^Q\.\s*", "", question).strip()

    dialogue = chunk[i_dlg + len("Dialogue") :]
    patient, doctor = "", ""
    if "Doctor:" in dialogue:
        i_doc = dialogue.find("Doctor:")
        patient = dialogue[:i_doc]
        doctor = dialogue[i_doc + len("Doctor:") :]
        patient = re.sub(r"^\s*Patient:\s*", "", patient).strip()
    else:
        patient = re.sub(r"^\s*Patient:\s*", "", dialogue).strip()

    # 去掉页脚广告与 URL，保留医学叙述
    def clean(s: str) -> str:
        s = re.sub(r"https?://\S+", " ", s)
        s = re.sub(r"-(?:>|&gt;)\s*", " ", s)  # icliniq 里的箭头替换
        s = re.sub(r"\s+", " ", s)
        return s.strip()

    question, patient, doctor = clean(question), clean(patient), clean(doctor)
    if not question and not patient:
        return None

    # icliniq 的 URL 形如 /qa/<topic-slug>/<question-slug>，第一段是疾病/主题
    topic_slug = ""
    m_url = re.search(r"/qa/([a-z0-9-]+)/", url)
    if m_url:
        topic_slug = m_url.group(1)

    # icliniq 的回答里常带「consult a <specialty> online」链接 → 科室线索
    # 必须在 clean() 抹掉 URL 之前，从原始对话里抓
    dept_hint = ""
    m_dept = re.search(r"ask-a-doctor-online/([a-z-]+)", dialogue)
    if m_dept:
        dept_hint = m_dept.group(1).replace("-", " ")
    else:
        m_dept2 = re.search(r"consult an? ([a-z]+) (?:online|specialist)", dialogue)
        if m_dept2:
            dept_hint = m_dept2.group(1)

    return {
        "qa_id": qa_id,
        "source": source,
        "url": url,
        "topic_slug": topic_slug,
        "department_hint": dept_hint,
        "question": question,
        "patient_text": patient[:1200],
        "doctor_text": doctor[:1200],
        "text": " ".join(x for x in (question, patient, doctor) if x)[:2500],
    }


def humanize_slug(slug: str) -> str:
    """disc-bulge -> Disc bulge；pcos -> Pcos（规范化时再统一为大写缩写）"""
    if not slug:
        return ""
    s = slug.replace("-", " ").strip()
    return s[:1].upper() + s[1:]


# --------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=300, help="抽样条数")
    ap.add_argument("--min-len", type=int, default=80, help="最短文本长度（过滤空对话）")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    files = sorted(RAW_DIR.glob("*.txt"))
    if not files:
        log(f"未找到原始数据：{RAW_DIR}")
        log("请先解压 Medical-Dialogue-Dataset-English.zip 到 data/raw/")
        return 1

    all_rows: list[dict] = []
    stats = {"files": {}, "parsed": 0, "dropped_short": 0, "dropped_dup": 0}

    for f in files:
        text = f.read_text(encoding="utf-8", errors="ignore")
        source = "healthcaremagic" if "healthcaremagic" in f.name else "icliniq"
        chunks = split_records(text)
        parsed = 0
        for ch in chunks:
            row = parse_record(ch, source)
            if not row:
                continue
            if len(row["text"]) < args.min_len:
                stats["dropped_short"] += 1
                continue
            all_rows.append(row)
            parsed += 1
        stats["files"][f.name] = {"records": len(chunks), "parsed": parsed}
        log(f"  {f.name:32s} 记录 {len(chunks):>7d}  有效 {parsed:>7d}")

    # 去重（同一问题文本只留一条）
    seen, uniq = set(), []
    for r in all_rows:
        h = hashlib.md5(r["question"].lower().encode()).hexdigest()
        if h in seen:
            stats["dropped_dup"] += 1
            continue
        seen.add(h)
        uniq.append(r)
    log(f"\n合计解析 {len(all_rows)} 条，去重后 {len(uniq)} 条")

    # 抽样：分源配额 + 每个疾病主题最多 3 条（保证既有规模也有重复实体可供融合）
    rnd = random.Random(args.seed)
    icliniq = [r for r in uniq if r["source"] == "icliniq" and r["topic_slug"]]
    hcm = [r for r in uniq if r["source"] == "healthcaremagic"]
    rnd.shuffle(icliniq)
    rnd.shuffle(hcm)

    quota_ic = int(args.limit * 0.8)
    quota_hc = args.limit - quota_ic

    by_topic: dict[str, int] = {}
    picked: list[dict] = []
    for r in icliniq:
        if by_topic.get(r["topic_slug"], 0) >= 3:
            continue
        by_topic[r["topic_slug"]] = by_topic.get(r["topic_slug"], 0) + 1
        r["disease_hint"] = humanize_slug(r["topic_slug"])
        picked.append(r)
        if len(picked) >= quota_ic:
            break
    picked.extend(hcm[:quota_hc])

    picked.sort(key=lambda x: x["qa_id"])
    stats.update(
        parsed=len(all_rows),
        unique=len(uniq),
        sampled=len(picked),
        with_disease_hint=sum(1 for r in picked if r.get("disease_hint")),
        topics=len({r["topic_slug"] for r in picked if r["topic_slug"]}),
        source_dist=dict(Counter(r["source"] for r in picked)),
        avg_text_len=round(sum(len(r["text"]) for r in picked) / max(1, len(picked)), 1),
    )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "qa_subset.jsonl"
    with out.open("w", encoding="utf-8") as f:
        for r in picked:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    (OUT_DIR / "preprocess_stats.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    log(f"\n抽样 {len(picked)} 条 -> {out}")
    log(f"  带疾病主题的样本 : {stats['with_disease_hint']}")
    log(f"  覆盖主题数       : {stats['topics']}")
    log(f"  来源分布         : {stats['source_dist']}")
    log(f"  平均文本长度     : {stats['avg_text_len']} 字符")
    log("\n样例：")
    r = picked[0]
    log(f"  [{r['qa_id']}] question={r['question'][:80]}")
    log(f"  disease_hint={r.get('disease_hint')} dept_hint={r['department_hint']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
