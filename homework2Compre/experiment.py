# ============================================================
# TuGraph 图分析完整脚本
# 包含: 基本统计 / 度数 / PageRank / 最短路径 / 节点分类 / t-SNE
# ============================================================

import numpy as np
import pandas as pd
import os
from neo4j import GraphDatabase
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, classification_report
from sklearn.manifold import TSNE
import matplotlib.pyplot as plt

# ============================================================
# 配置区: 改这里切换数据库
# ============================================================
BOLT_URL   = "bolt://localhost:7687"
USER       = "admin"
PASSWORD   = "73@TuGraph"

GRAPH_NAME = "default"      # 目标图名
NODE_LABEL = "node"         # 点类型
EDGE_TYPE  = "edge"         # 边类型, 没有就设 None
FEATURE_KEY = "feature"     # 特征字段名

OUTPUT_DIR = f"./output_{GRAPH_NAME}"
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ============================================================
# 工具函数
# ============================================================
def get_driver():
    return GraphDatabase.driver(BOLT_URL, auth=(USER, PASSWORD))


def run_query(query, params=None):
    driver = get_driver()
    with driver.session(database=GRAPH_NAME) as session:
        result = session.run(query, params or {})
        rows = [dict(r) for r in result]
    driver.close()
    return rows


# ============================================================
# 1. 基本统计
# ============================================================
def basic_stats():
    q_nodes = f"MATCH (n:{NODE_LABEL}) RETURN count(n) AS c"
    n_nodes = run_query(q_nodes)[0]["c"]

    n_edges = 0
    if EDGE_TYPE:
        q_edges = f"MATCH ()-[r:{EDGE_TYPE}]->() RETURN count(r) AS c"
        n_edges = run_query(q_edges)[0]["c"]
    else:
        q_edges = "MATCH ()-[r]->() RETURN count(r) AS c"
        n_edges = run_query(q_edges)[0]["c"]

    print(f"[{GRAPH_NAME}] Nodes: {n_nodes}")
    print(f"[{GRAPH_NAME}] Edges: {n_edges}")

    with open(f"{OUTPUT_DIR}/basic_stats.txt", "w") as f:
        f.write(f"graph={GRAPH_NAME}\n")
        f.write(f"nodes={n_nodes}\nedges={n_edges}\n")

    return n_nodes, n_edges


# ============================================================
# 2. 度数统计
# ============================================================
def degree_stats():
    if EDGE_TYPE:
        query = f"""
        MATCH (n:{NODE_LABEL})-[r:{EDGE_TYPE}]-()
        RETURN n.node_id AS node_id, count(r) AS degree
        """
    else:
        query = f"""
        MATCH (n:{NODE_LABEL})-[r]-()
        RETURN n.node_id AS node_id, count(r) AS degree
        """

    rows = run_query(query)
    df = pd.DataFrame(rows)
    print(f"\n[{GRAPH_NAME}] Degree stats:")
    print(df["degree"].describe())
    df.to_csv(f"{OUTPUT_DIR}/degrees.csv", index=False)
    return df


# ============================================================
# 3. PageRank
# ============================================================
def run_pagerank():
    query = """
    CALL algo.pagerank(20)
    YIELD node, pr
    RETURN node.node_id AS node_id, pr
    ORDER BY pr DESC
    LIMIT 10
    """
    rows = run_query(query)
    df = pd.DataFrame(rows)
    print(f"\n[{GRAPH_NAME}] PageRank Top 10:")
    print(df)
    df.to_csv(f"{OUTPUT_DIR}/pagerank_top10.csv", index=False)
    return df


# ============================================================
# 4. 最短路径
# ============================================================
def run_shortest_path(src_id, dst_id):
    query = f"""
    MATCH (a:{NODE_LABEL} {{node_id: $src}}), (b:{NODE_LABEL} {{node_id: $dst}})
    WITH a, b
    CALL algo.shortestPath(a, b)
    YIELD nodeCount, totalCost, path
    RETURN nodeCount, totalCost
    """
    rows = run_query(query, {"src": src_id, "dst": dst_id})
    if rows:
        print(f"\n[{GRAPH_NAME}] Shortest path {src_id} -> {dst_id}:")
        print(rows[0])
        with open(f"{OUTPUT_DIR}/shortest_path.txt", "w") as f:
            f.write(f"{src_id} -> {dst_id}\n")
            f.write(str(rows[0]))
    else:
        print(f"\n[{GRAPH_NAME}] No shortest path found")


# ============================================================
# 5. 节点分类（需要 feature）
# ============================================================
def run_node_classification(deg_df):
    query = f"""
    MATCH (n:{NODE_LABEL})
    WHERE n.{FEATURE_KEY} IS NOT NULL
    RETURN n.node_id AS node_id, n.{FEATURE_KEY} AS feature
    """
    rows = run_query(query)
    print(f"\n[{GRAPH_NAME}] Nodes with feature: {len(rows)}")

    if len(rows) == 0:
        print(f"[{GRAPH_NAME}] No feature found, skip node classification.")
        return None

    node_ids, vectors = [], []
    for r in rows:
        node_ids.append(r["node_id"])
        vectors.append([float(x) for x in r["feature"].split()])

    X = np.vstack(vectors)
    print(f"[{GRAPH_NAME}] Feature matrix: {X.shape}")

    # 度数分 3 类
    deg_df["label"] = pd.qcut(deg_df["degree"], q=3, labels=[0, 1, 2])
    deg_map = dict(zip(deg_df["node_id"], deg_df["label"]))

    labels = np.array([deg_map.get(nid, -1) for nid in node_ids])
    mask = labels != -1
    X, labels = X[mask], labels[mask]

    if len(X) < 10:
        print(f"[{GRAPH_NAME}] Not enough labeled nodes, skip.")
        return None

    # 划分
    X_train, X_test, y_train, y_test = train_test_split(
        X, labels, test_size=0.2, random_state=42, stratify=labels
    )

    # 训练
    clf = LogisticRegression(max_iter=1000)
    clf.fit(X_train, y_train)
    y_pred = clf.predict(X_test)

    acc = accuracy_score(y_test, y_pred)
    print(f"\n[{GRAPH_NAME}] Node classification accuracy: {acc:.4f}")
    report = classification_report(
        y_test, y_pred, target_names=["low", "medium", "high"]
    )
    print(report)

    with open(f"{OUTPUT_DIR}/classification_report.txt", "w") as f:
        f.write(f"Accuracy: {acc:.4f}\n\n")
        f.write(report)

    return X, labels


# ============================================================
# 6. t-SNE 可视化
# ============================================================
def run_tsne(X, labels, sample_size=2000):
    if len(X) > sample_size:
        idx = np.random.choice(len(X), sample_size, replace=False)
        X_s, y_s = X[idx], labels[idx]
    else:
        X_s, y_s = X, labels

    tsne = TSNE(n_components=2, random_state=42, perplexity=30)
    X_2d = tsne.fit_transform(X_s)

    plt.figure(figsize=(8, 6))
    scatter = plt.scatter(X_2d[:, 0], X_2d[:, 1], c=y_s, cmap="viridis", s=10)
    plt.colorbar(scatter, label="Degree class")
    plt.title(f"{GRAPH_NAME} t-SNE of DeepWalk Embeddings")
    plt.savefig(f"{OUTPUT_DIR}/tsne.png", dpi=150)
    plt.close()
    print(f"[{GRAPH_NAME}] t-SNE saved to {OUTPUT_DIR}/tsne.png")


# ============================================================
# 主流程
# ============================================================
def main():
    print("=" * 60)
    print(f"Graph analysis: graph={GRAPH_NAME}, label={NODE_LABEL}")
    print("=" * 60)

    # 1. 基本统计
    basic_stats()

    # 2. 度数
    deg_df = degree_stats()

    # 3. PageRank
    pr_df = run_pagerank()

    # 4. 最短路径：用 PageRank Top2 节点
    if len(pr_df) >= 2:
        src = int(pr_df.iloc[0]["node_id"])
        dst = int(pr_df.iloc[1]["node_id"])
        run_shortest_path(src, dst)

    # 5. 节点分类（有 feature 才跑）
    result = run_node_classification(deg_df)

    # 6. t-SNE
    if result is not None:
        X, labels = result
        run_tsne(X, labels)

    print(f"\n[{GRAPH_NAME}] All done. Results in {OUTPUT_DIR}/")


if __name__ == "__main__":
    main()