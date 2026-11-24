"""第 3 步 · 基础 Text2Cypher 模块。

链路（PPT 第 25 页的最小闭环）：
  自然语言问题 -> 组装 Schema 感知 Prompt -> 大模型生成 Cypher
  -> 只读安全检查 -> TuGraph(Bolt) 执行 -> 返回结果 + 来源(证据)

三种可切换配置（供第 4 步做基线与消融）：
  mode="baseline"     不给 Schema、不给示例（模拟「裸」大模型用法）
  mode="schema"       给 Schema 与规则，不给示例
  mode="schema_aware" 给 Schema + 规则 + few-shot 示例（默认）

用法：
  from text2cypher import Text2Cypher
  t2c = Text2Cypher(graph="medical_kg_en")
  out = t2c.ask("红癣病有哪些症状？")
"""
from __future__ import annotations

import json
import re
import time

from kg_common import TuGraphClient, LLMClient, get_config

READONLY_KEYWORDS = ("create", "delete", "set ", "merge", "remove", "drop", "call ",
                     "load csv", "foreach", "detach")

SCHEMA_SYSTEM = """你是 TuGraph 图数据库的 Cypher 生成助手。只能输出一条只读 Cypher 语句，不要解释。
硬性规则：
1. 只允许 MATCH / WHERE / RETURN / WITH / ORDER BY / LIMIT / UNWIND / AS / IN；
2. 严禁 CREATE、MERGE、SET、DELETE、REMOVE、DROP、CALL、LOAD CSV 等写操作；
3. 只能使用下面给出的节点标签、关系类型与属性，不得臆造；
4. 实体名称是英文小写字符串，用 n.name = 'xxx' 精确匹配；
5. 关系有方向：关系类型已经表达了语义（如 HAS_SYMPTOM 表示 Disease -> Symptom），不要反向使用；
6. 需要证据时同时返回 r.qa_id、r.evidence；
7. 若问题涉及图谱中不存在的关系（例如「疾病属于哪个医院」），也照常生成最接近的只读查询；
8. 只输出 Cypher 语句本身，不要 ``` 代码块。"""

BASELINE_SYSTEM = """你是图数据库助手。请把用户的中文问题转换成 Cypher 查询语句并直接输出，不要解释。"""

FEW_SHOT = [
    (
        "高血压有哪些症状？",
        "MATCH (d:Disease)-[r:HAS_SYMPTOM]->(s:Symptom) "
        "WHERE d.name = 'hypertension' RETURN s.name AS symptom, r.qa_id AS source LIMIT 20",
    ),
    (
        "哮喘需要做哪些检查？",
        "MATCH (d:Disease)-[r:REQUIRES_EXAM]->(e:Examination) "
        "WHERE d.name = 'asthma' RETURN e.name AS examination, r.evidence AS evidence LIMIT 20",
    ),
    (
        "图谱里一共有多少疾病、多少症状？",
        "MATCH (d:Disease) WITH count(d) AS diseases MATCH (s:Symptom) "
        "RETURN diseases, count(s) AS symptoms",
    ),
]


class Text2Cypher:
    def __init__(self, graph: str | None = None, mode: str = "schema_aware",
                 guard: bool = True, limit: int = 20):
        cfg = get_config()
        self.graph = graph or cfg["TGRAPH_GRAPH_EN"]
        self.mode = mode
        self.guard = guard
        self.limit = limit
        self.client = TuGraphClient(self.graph)
        self.llm = LLMClient()
        self._schema_text: str | None = None
        self._samples: dict[str, list[str]] | None = None

    # ------------------------------------------------------------------
    # Schema 感知：直接读 TuGraph 的 graph schema（不是手写常量）
    # ------------------------------------------------------------------
    def schema_text(self) -> str:
        if self._schema_text is not None:
            return self._schema_text
        raw = self.client.run("CALL dbms.graph.getGraphSchema()")[0]["schema"]
        schema = json.loads(raw)["schema"]
        lines = [f"图名：{self.graph}"]
        labels = [s["label"] for s in schema if s.get("type") == "VERTEX"]
        lines.append("节点标签与属性：")
        for s in schema:
            if s.get("type") != "VERTEX":
                continue
            props = ", ".join(p["name"] for p in s.get("properties", []))
            lines.append(f"  (:{s['label']} {{ {props} }})  主键 {s.get('primary')}")
        lines.append("关系类型（起点 -> 终点，属性）：")
        self._rel_pairs = {}
        for s in schema:
            if s.get("type") != "EDGE":
                continue
            pairs = s.get("constraints") or s.get("pairs") or []
            props = ", ".join(p["name"] for p in s.get("properties", []))
            desc = ", ".join(":".join(map(str, p)) for p in pairs) if pairs else "?"
            lines.append(f"  [:{s['label']} {{ {props} }}]  {desc}")
            for p in pairs:
                if isinstance(p, (list, tuple)) and len(p) == 2:
                    self._rel_pairs.setdefault(s["label"], []).append(tuple(p))
        if not getattr(self, "_rel_pairs", None):
            # 兜底：schema 没给约束时用固定映射
            self._rel_pairs = {"HAS_SYMPTOM": [("Disease", "Symptom")],
                               "USES_DRUG": [("Disease", "Drug")],
                               "BELONGS_TO": [("Disease", "Department")],
                               "REQUIRES_EXAM": [("Disease", "Examination")]}
        self._schema_text = "\n".join(lines)
        return self._schema_text

    def sample_values(self, per_label: int = 4) -> dict[str, list[str]]:
        if self._samples is not None:
            return self._samples
        out = {}
        for lab in ("Disease", "Symptom", "Drug", "Examination", "Department"):
            rows = self.client.run(
                f"MATCH (n:{lab}) RETURN n.name AS name ORDER BY n.name LIMIT {per_label}"
            )
            out[lab] = [r["name"] for r in rows if r.get("name")]
        self._samples = out
        return out

    # ------------------------------------------------------------------
    def build_prompt(self, question: str) -> tuple[str, str]:
        if self.mode == "baseline":
            return BASELINE_SYSTEM, f"问题：{question}\nCypher："

        parts = [self.schema_text()]
        samples = self.sample_values()
        if samples:
            parts.append("样例实体名（仅用于理解命名风格，不要照抄）：")
            for lab, names in samples.items():
                if names:
                    parts.append(f"  {lab}: {', '.join(names[:4])}")
        parts.append(f"答案最多返回 {self.limit} 行。")
        system = SCHEMA_SYSTEM + "\n\n" + "\n".join(parts)

        user = ""
        if self.mode == "schema_aware":
            user += "参考示例：\n"
            for q, cy in FEW_SHOT:
                user += f"问题：{q}\nCypher：{cy}\n"
            user += "\n"
        user += f"问题：{question}\nCypher："
        return system, user

    # ------------------------------------------------------------------
    @staticmethod
    def clean_cypher(text: str) -> str:
        """把模型输出整理成单条 Cypher。

        注意：模型常把一条查询写成多行，不能按行截取，否则会丢掉 WHERE/RETURN。
        """
        t = re.sub(r"```(?:cypher)?", "", (text or "")).strip("`").strip()
        # 多行合并成一行
        t = re.sub(r"\s+", " ", t).strip()
        # 只取第一条语句
        t = t.split(";")[0].strip()
        m = re.search(r"\b(MATCH|WITH|UNWIND|RETURN)\b", t, re.I)
        if m and m.start() > 0:
            t = t[m.start():]
        return t

    @staticmethod
    def is_readonly(cypher: str) -> tuple[bool, str]:
        low = " " + re.sub(r"\s+", " ", cypher.lower()) + " "
        for kw in READONLY_KEYWORDS:
            if kw in low:
                return False, f"包含写操作关键字「{kw.strip()}」"
        if not low.strip().startswith(("match", "with", "return", "unwind")):
            return False, "不是以 MATCH/WITH/RETURN 开头的查询"
        return True, ""

    # ------------------------------------------------------------------
    def ask(self, question: str) -> dict:
        """生成 + 校验 + 执行，返回结构化结果。"""
        t0 = time.time()
        system, user = self.build_prompt(question)
        raw = self.llm.chat(system, user, max_tokens=400)
        cypher = self.clean_cypher(raw)
        res = {"question": question, "mode": self.mode, "raw": raw, "cypher": cypher,
               "guarded": self.guard, "ok": False, "rows": [], "error": ""}
        if not cypher:
            res["error"] = "模型未返回 Cypher"
            return res
        if self.guard:
            passed, reason = self.is_readonly(cypher)
            if not passed:
                res["error"] = f"安全检查拦截：{reason}"
                return res
        try:
            res["rows"] = self.client.run(cypher)
            res["ok"] = True
        except Exception as e:  # noqa: BLE001
            res["error"] = str(e).splitlines()[0][:200]
        res["latency"] = round(time.time() - t0, 2)
        return res

    def close(self):
        self.client.close()
