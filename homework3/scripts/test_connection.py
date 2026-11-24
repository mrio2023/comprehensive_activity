"""第 1 步 · 环境部署：TuGraph Python 客户端连接测试。

流程：
  1. 读取 .env 配置
  2. Bolt 握手 + 认证
  3. 列出图、查询目标图是否存在
  4. 打印服务端版本信息与延迟

运行：python3 scripts/test_connection.py
"""
from __future__ import annotations

import sys
import time

from kg_common import TuGraphClient, get_config, log


def main() -> int:
    cfg = get_config()
    log("=" * 66)
    log(" TuGraph 连接测试")
    log("=" * 66)
    log(f"  Bolt 地址 : {cfg['TGRAPH_URI']}")
    log(f"  用户名    : {cfg['TGRAPH_USER']}")
    log(f"  目标图    : {cfg['TGRAPH_GRAPH']}")

    t0 = time.time()
    client = TuGraphClient()
    try:
        # 1) 握手认证（第一次查询即验证账号密码）
        ver = client.run("CALL dbms.config.list()")
        latency = (time.time() - t0) * 1000
        log(f"\n[1] 握手认证成功，耗时 {latency:.0f} ms")

        info = {d.get("name"): d.get("value") for d in ver if isinstance(d, dict)}
        if info:
            log(f"[2] 服务端配置共 {len(info)} 项，节选:")
            keys = [k for k in info if any(x in k.lower() for x in ("version", "port", "data_dir", "max_"))]
            for k in keys[:10]:
                log(f"      {k:28s} = {info[k]}")

        # 2) 列出已有图（TuGraph 支持多图；Bolt 的 database 字段即图名）
        log("\n[3] 图列表（尝试已知图名）:")
        for g in ("medical_kg", cfg["TGRAPH_GRAPH_EN"], "amazon", "dblp"):
            try:
                c = TuGraphClient(graph=g)
                n, e = c.count_nodes(), c.count_edges()
                c.close()
                log(f"      {g:18s} 存在   节点 {n:>7d}   关系 {e:>7d}")
            except Exception as ex:
                msg = str(ex).splitlines()[0]
                log(f"      {g:18s} 不可用  ({msg[:60]})")

        # 3) 目标图连通性检查
        log("\n[4] 目标图连通性:")
        if client.exists():
            n = client.count_nodes()
            log(f"      {client.graph} 可查询，当前节点数 {n}")
            log("      样例语句：MATCH (n) RETURN count(n)  ->  OK")
        else:
            log(f"      图 {client.graph} 不存在，请先在管理台新建（http://127.0.0.1:7070）")

        log("\n结论：TuGraph 与本地 Python(Bolt) 连接正常。")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
