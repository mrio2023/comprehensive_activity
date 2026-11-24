"""公共工具：.env 读取、TuGraph(Bolt) 连接、大模型调用。

依赖：neo4j(Python 驱动)、requests（conda normal 环境已内置）。
用法：from kg_common import get_config, TuGraphClient, LLMClient
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------
# .env
# --------------------------------------------------------------------------
def load_env(path: Path | None = None) -> dict:
    """极简 .env 解析（不引入 python-dotenv，保持依赖最小）。"""
    path = path or (ROOT / ".env")
    cfg: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            cfg[k.strip()] = v.strip().strip('"').strip("'")
    # 环境变量优先于 .env
    for k in list(cfg):
        if k in os.environ:
            cfg[k] = os.environ[k]
    return cfg


def get_config() -> dict:
    cfg = load_env()
    cfg.setdefault("TGRAPH_URI", "bolt://127.0.0.1:7687")
    cfg.setdefault("TGRAPH_USER", "admin")
    cfg.setdefault("TGRAPH_PASSWORD", "73@TuGraph")
    cfg.setdefault("TGRAPH_GRAPH", "medical_kg")
    cfg.setdefault("TGRAPH_GRAPH_EN", "medical_kg_en")
    cfg.setdefault("LLM_TEMPERATURE", "0")
    cfg.setdefault("LLM_TIMEOUT", "120")
    return cfg


# --------------------------------------------------------------------------
# TuGraph：Bolt 连接
# --------------------------------------------------------------------------
class TuGraphClient:
    """TuGraph 的 Bolt 客户端。

    说明：TuGraph 把「图名」当作 Bolt 协议里的 database 字段，
    因此 driver.session(database=<图名>) 即切换到对应图。
    这与 Web 管理台（127.0.0.1:7070，登录用的也是 bolt://127.0.0.1:7687）一致。
    """

    def __init__(self, graph: str | None = None):
        from neo4j import GraphDatabase

        self.cfg = get_config()
        self.graph = graph or self.cfg["TGRAPH_GRAPH"]
        self.driver = GraphDatabase.driver(
            self.cfg["TGRAPH_URI"],
            auth=(self.cfg["TGRAPH_USER"], self.cfg["TGRAPH_PASSWORD"]),
        )

    def close(self):
        self.driver.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def run(self, cypher: str, params: dict | None = None, retries: int = 2) -> list[dict]:
        """执行一条 Cypher，返回 list[dict]。

        注：TuGraph 在极少数语句（如返回空结果集的 LIMIT 0）上会让 Bolt 连接断掉，
        这里做一次重连重试，避免整条流水线中断。
        """
        last = None
        for attempt in range(retries + 1):
            try:
                with self.driver.session(database=self.graph) as s:
                    result = s.run(cypher, params or {})
                    return [r.data() for r in result]
            except Exception as e:  # noqa: BLE001
                last = e
                if "defunct" not in str(e) and "ServiceUnavailable" not in type(e).__name__:
                    raise
                time.sleep(0.5 * (attempt + 1))
        raise last

    def exists(self) -> bool:
        try:
            self.run("MATCH (n) RETURN count(n) AS c")
            return True
        except Exception:
            return False

    def ensure_graph(self, description: str = "", max_size_gb: int = 10) -> bool:
        """图不存在时通过 CALL dbms.graph.createGraph 创建。返回是否新建。"""
        if self.exists():
            return False
        admin = TuGraphClient(graph="medical_kg" if self.graph != "medical_kg" else "amazon")
        try:
            admin.run(
                f"CALL dbms.graph.createGraph('{self.graph}','{description}',{max_size_gb})"
            )
        finally:
            admin.close()
        return True

    def count_nodes(self) -> int:
        return self.run("MATCH (n) RETURN count(n) AS c")[0]["c"]

    def count_edges(self) -> int:
        return self.run("MATCH ()-[r]->() RETURN count(r) AS c")[0]["c"]

    def labels(self) -> list[dict]:
        return self.run("CALL db.vertexLabels()")

    def edge_labels(self) -> list[dict]:
        return self.run("CALL db.edgeLabels()")


# --------------------------------------------------------------------------
# 大模型：OpenAI 兼容的 /chat/completions
# --------------------------------------------------------------------------
class LLMClient:
    def __init__(self, cfg: dict | None = None, temperature: float | None = None):
        self.cfg = cfg or get_config()
        self.base_url = self.cfg["LLM_BASE_URL"].rstrip("/")
        self.api_key = self.cfg.get("LLM_API_KEY", "")
        self.model = self.cfg.get("LLM_MODEL", "gpt-6.2-sol")
        self.temperature = (
            float(temperature) if temperature is not None else float(self.cfg["LLM_TEMPERATURE"])
        )
        self.timeout = int(self.cfg["LLM_TIMEOUT"])
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0

    def available(self) -> bool:
        return bool(self.api_key) and self.api_key.startswith("sk-") and "xxxx" not in self.api_key

    def chat(self, system: str, user: str, retries: int = 3, max_tokens: int = 1200) -> str:
        import requests

        if not self.available():
            raise RuntimeError("未配置 LLM_API_KEY，请在 .env 中填写")
        payload = {
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        last_err = None
        for attempt in range(retries):
            try:
                t0 = time.time()
                resp = requests.post(
                    f"{self.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=self.timeout,
                )
                if resp.status_code != 200:
                    last_err = f"HTTP {resp.status_code}: {resp.text[:200]}"
                    time.sleep(1.5 * (attempt + 1))
                    continue
                data = resp.json()
                usage = data.get("usage") or {}
                self.calls += 1
                self.prompt_tokens += usage.get("prompt_tokens", 0)
                self.completion_tokens += usage.get("completion_tokens", 0)
                self.last_latency = round(time.time() - t0, 2)
                return data["choices"][0]["message"]["content"] or ""
            except Exception as e:  # 网络抖动重试
                last_err = repr(e)[:200]
                time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"大模型调用失败：{last_err}")


# --------------------------------------------------------------------------
# 写入辅助：逐条参数化 MERGE
# --------------------------------------------------------------------------
# 实测：TuGraph 的 UNWIND + SET 组合会把行值错位（相邻行互相覆盖），
# 因此写入统一改为「一条 Cypher 一个参数集」，规模几百~几千条时性能足够（约 1ms/条）。
def upsert_vertices(client, label: str, rows: list[dict]) -> int:
    cypher = (
        f"MERGE (n:{label} {{id: $id}}) "
        f"SET n.name = $name, n.description = $description"
    )
    for r in rows:
        client.run(
            cypher,
            {
                "id": str(r["id"]),
                "name": str(r.get("name", r["id"])),
                "description": str(r.get("description") or ""),
            },
        )
    return len(rows)


def upsert_edges(client, src: str, rel: str, dst: str, rows: list[dict]) -> int:
    cypher = (
        f"MATCH (a:{src} {{id: $src_id}}) "
        f"MATCH (b:{dst} {{id: $dst_id}}) "
        f"MERGE (a)-[r:{rel}]->(b) "
        f"SET r.qa_id = $qa_id, r.evidence = $evidence, r.scope = $scope"
    )
    for r in rows:
        client.run(
            cypher,
            {
                "src_id": str(r["src_id"]),
                "dst_id": str(r["dst_id"]),
                "qa_id": str(r.get("qa_id") or ""),
                "evidence": str(r.get("evidence") or ""),
                "scope": str(r.get("scope") or "general"),
            },
        )
    return len(rows)


# --------------------------------------------------------------------------
# JSON 解析（模型常带 ```json 包裹或前后废话）
# --------------------------------------------------------------------------
def parse_json_block(text: str):
    """从模型输出中尽力抽出 JSON（对象或数组）。失败返回 None。"""
    if not text:
        return None
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    # 去掉首尾多余字符后再试
    for opener, closer in (("[", "]"), ("{", "}")):
        i, j = text.find(opener), text.rfind(closer)
        if i != -1 and j > i:
            try:
                return json.loads(text[i : j + 1])
            except Exception:
                continue
    return None


def log(msg: str):
    print(msg, flush=True)
