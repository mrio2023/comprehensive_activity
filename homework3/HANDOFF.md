# 作业三 · 交接文档

> 面向：接手把实验跑完、写报告并提交的人
> 更新：本次会话结束时；代码在 `homework3/`，仓库根目录 `/home/mrio/Codes/homework_compre`（remote：`git@github.com:mrio2023/comprehensive_activity.git`）

---

## 0. 一句话现状

**第 1、2 步已完整跑通并产出结果，第 3 步（Text2Cypher）代码完成且实测可用，第 4 步只剩「跑一次对比实验」，第 5 步（报告 + 截图 + 提交）完全没做。**
也就是说：**代码基本齐了，缺的是最后一次批量实验和文档。**

| PPT 评分项 | 状态 | 产物 |
|---|---|---|
| 1. 环境部署（TuGraph + Python 客户端） | ✅ 完成 | `scripts/test_connection.py`，Bolt 连接实测通过 |
| 2. 图谱构建（清洗/抽取/融合/导入/质量） | ✅ 完成 | 12 个脚本 + `results/*.json`，两张图已入库 |
| 3. 基础 Text2Cypher + 交互问答 | ✅ 代码完成、单条实测通过 | `scripts/text2cypher.py`、`scripts/qa_cli.py` |
| 4. 功能测试与对比（Schema 感知 / 基线 / 消融） | ⚠️ **脚本就绪，实验被中断没跑完** | `scripts/eval_text2cypher.py`（无产出文件） |
| 5. GitHub 报告（截图 + 说明） | ❌ 未开始 | 需写 `README.md`，截 TuGraph 管理台图 |

---

## 1. 环境（不要重装、不要新建 venv）

| 项 | 值 |
|---|---|
| 机器 | Ubuntu 26.04，i7-12700H / 15GB RAM / RTX 3050 4GB |
| Python | **`/home/mrio/miniconda3/envs/normal/bin/python`（3.11）**，脚本全部按这个环境写（有 `neo4j 6.4.0`、`requests`、`pandas`） |
| TuGraph | Docker 容器 **`tugraph_demo`**，镜像 `tugraph/tugraph-runtime-centos7:latest`，端口 7070(Web) / 7687(Bolt) / 9090(RPC) |
| 账号 | `admin` / `73@TuGraph` |
| 大模型 | OpenAI 兼容接口，配置在 `homework3/.env`（**已被 .gitignore，不进仓库**）：`LLM_BASE_URL=https://drc.totokens.cc/v1`，模型 `gpt-6.2-sol`，key 已填好可用 |
| 已有图 | `medical_kg`（中文课堂小样例 8 点/6 边）、`medical_kg_en`（英文真实图谱 622 点/416 边）、以及上周作业的 `amazon`、`dblp` |

启动 TuGraph（容器退出后数据不丢，重启即可）：

```bash
docker start tugraph_demo && sleep 12
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:7070   # 期望 200
```

> ⚠️ 容器要是挂了（`Exited (139)`），直接 `docker start tugraph_demo` 重启，数据在容器层里还在（已验证过一次）。

---

## 2. 数据

- 原始数据：`homework3/Medical-Dialogue-Dataset-English.zip`（90MB，英文 MedDialog，**不是天池中文版**，因为天池 2.24GB 需要登录审批）
- 已解压到 `data/raw/Medical-Dialogue-Dataset-English/`（296MB，已在 `.gitignore` 里，**不要提交**）
- 解析结果：257,469 条记录 → 去重 228,879 条 → **抽样 150 条**（120 条 icliniq + 30 条 healthcaremagic，覆盖 115 个疾病主题）→ `data/processed/qa_subset.jsonl`

原因说明（写报告时用得上）：英文版是同一个 MedDialog 数据集，字段结构一致（id / URL / Description / Dialogue），且 icliniq 的 URL 自带疾病主题、回答里带专科链接，反而比中文版更容易做「疾病 → 症状/检查/科室」的实体与关系抽取；中文对照用 `medical_kg` 小图保留 PPT 的验收口径（糖尿病 → 多饮/多尿）。

---

## 3. 代码与产物清单

```
homework3/
├── .env / .env.example           # 大模型与数据库配置（.env 不进仓库）
├── .gitignore
├── Medical-Dialogue-Dataset-English.zip
├── 专业综合实践-实验3-*.pptx      # 作业原题
├── data/
│   ├── raw/                      # 解压后的英文语料（.gitignore）
│   ├── demo/{nodes,edges}.csv    # 课堂小样例：8 节点 6 关系（中文）
│   ├── dictionary/terms.json     # 规则抽取词典 487 词条 + 同义规范化表
│   ├── annotations/gold_12.jsonl # 人工参考标注 12 条（实体+关系）
│   ├── processed/                # 预处理/抽取/融合的中间产物 + kg_nodes.csv / kg_edges.csv
│   └── testset/text2cypher_testset.json  # 11 条 Text2Cypher 测试集（含标准答案）
├── scripts/                      # 13 个脚本，共约 2200 行
└── results/                      # 已产出的实验指标
```

| 脚本 | 作用 | 是否已跑 |
|---|---|---|
| `kg_common.py` | 公共库：.env 解析、Bolt 客户端、LLM 客户端、JSON 提取、写入辅助 | — |
| `test_connection.py` | 第 1 步：连接测试（含图列表） | ✅ |
| `init_schema.py` | 5 类点 + 4 类边（图不存在时自动建） | ✅ |
| `import_demo.py` | 导入课堂 8 点/6 边样例 | ✅ |
| `verify_graph.py` | 自动验证（规模/标签/属性/业务查询），**9/9 通过** | ✅ |
| `preprocess_data.py` | 解析两种原始格式 + 清洗 + 分层抽样 | ✅ |
| `extract_ner.py` | NER：`--method rule_simple / rule_longest / llm` | ✅（规则 150 条，LLM 50 条） |
| `extract_re.py` | RE：`--method rule / llm` | ✅（规则 150 条，LLM 50 条） |
| `evaluate_extraction.py` | 与人工标注比 P/R/F1，输出 `evaluation_errors_ner.csv` | ✅ |
| `build_kg.py` | 规范化 + 融合去重 + 写入 `medical_kg_en` | ✅ |
| `quality_score.py` | 六维质量评分 | ✅ |
| `text2cypher.py` | Text2Cypher 核心：Schema 感知 prompt、只读校验、执行 | ✅ |
| `qa_cli.py` | 交互式问答（`-q` 可单问，便于截图） | ✅ 实测通过 |
| `build_testset.py` | 生成 11 条测试集（含标准答案） | ✅ |
| `eval_text2cypher.py` | **第 4 步对比实验（baseline / schema / schema_aware）** | ❌ **没跑完，见第 5 节** |

---

## 4. 已有实验结果（可直接抄进报告）

### 4.1 课堂样例图 `medical_kg`（PPT 验收口径）
节点 8 / 关系 6，5 类点 4 类边，`verify_graph.py` **9/9 项通过**，含：
`MATCH (d:Disease)-[r:HAS_SYMPTOM]->(s:Symptom) WHERE d.name='糖尿病'` → 多饮、多尿。

### 4.2 真实图谱 `medical_kg_en`（`results/build_kg_stats.json`）
- 实体：原始 1,144 个 → 规范化融合后 **622**（Disease 235 / Symptom 169 / Drug 107 / Examination 73 / Department 38）
- 关系：原始 426 条 → 融合后 **416**（HAS_SYMPTOM 177 / REQUIRES_EXAM 86 / USES_DRUG 84 / BELONGS_TO 69），其中 `case_specific`（情境性建议）134 条

### 4.3 抽取质量（`results/eval_ner.md` / `eval_re.md`，人工标注 12 条）

| 方法 | P | R | F1 |
|---|---|---|---|
| rule_simple（简单子串） | 57.58% | 38.78% | 46.34% |
| rule_longest（最长匹配优先） | 59.38% | 38.78% | 46.91% |
| llm（大模型） | 43.94% | 59.18% | **50.43%** |

| 关系方法 | P | R | F1 |
|---|---|---|---|
| rule | 20.00% | 3.70% | 6.25% |
| llm | 25.81% | 29.63% | **27.59%** |

> 这三个数字正好回答 PPT 第 19 页的三问：最长匹配只把 F1 抬高 0.57 个点（只解决位置重叠，解决不了词典外实体）；召回低是因为词典覆盖不全（`evaluation_errors_ner.csv` 里能看到 lyme disease、hematoma 这类漏抽）；大模型召回高但精确率低（会多抽"covid""allergy"这类语境实体），说明仍需人工校验。

### 4.4 图谱质量（`results/quality_score.md`）

| 维度 | 得分 | 权重 |
|---|---|---|
| S 结构完整性 | 100.00% | 0.15 |
| C 证据完备 | 100.00% | 0.15 |
| N 实体规范 | 99.68% | 0.20 |
| R 关系有效 | 25.81% | 0.25 |
| T 可溯源 | 99.52% | 0.15 |
| V 覆盖度 | 100.00% | 0.10 |
| **Q** | **0.8132** | — |

> R 维度 = 结构合法率 × 人工抽样精确率，故意不给满分：结构合法只是必要条件，关系语义准确性才是短板（写报告时要点明，别自评 0.99 那种分数，老师一眼看穿）。

---

## 5. 待办清单（按顺序做，估计 1.5~2 小时）

### ① 跑完第 4 步对比实验（30~40 分钟，主要瓶颈是模型延迟）

```bash
cd /home/mrio/Codes/homework_compre/homework3
timeout 2400 /home/mrio/miniconda3/envs/normal/bin/python scripts/eval_text2cypher.py \
  --modes baseline schema schema_aware
```

- 11 条问题 × 3 档配置 = 33 次调用；单次 2~15s，**总耗时约 10~25 分钟**（上次跑到一半被手动中断了，不是报错）
- 产出：`results/text2cypher_eval.json` + `results/text2cypher_eval.md`（报告直接引用这张表）
- 中途断了可以分档跑：`--modes baseline`、`--modes schema_aware`，结果会各自覆盖写 json（**注意：分档跑会覆盖，要么一次跑完，要么自己合并 json**）
- 预期结论（写在报告里当"预期"先写，跑完再核对）：`baseline` 合法率低（模型不知道图和关系名，会写 `HIGH_BLOOD_PRESSURE` 之类）、`schema_aware` 准确率最高、`schema` 介于两者之间（说明 few-shot 主要解决"实体名用英文小写"和"关系方向"）。

### ② 补几个数字（5 分钟，可选但建议）
```bash
# 关系/实体规模、示例查询，用于报告正文
python3 -c "
import sys; sys.path.insert(0,'scripts')
from kg_common import TuGraphClient
c=TuGraphClient('medical_kg_en')
print(c.run('MATCH (n) RETURN count(n) AS n'))
print(c.run(\"MATCH (d:Disease)-[r:HAS_SYMPTOM]->(s:Symptom) WHERE d.name='scabies' RETURN s.name AS s, r.evidence AS e\"))
"
```

### ③ 截图（15 分钟）—— 作业要求"操作步骤要展示截图并附带文字说明"

浏览器打开 `http://127.0.0.1:7070`，用 `bolt://127.0.0.1:7687` + `admin` / `73@TuGraph` 登录（会弹"默认密码风险"提示，点**跳过**）。建议截 4 张，存到 `docs/screenshots/`：

| 截图 | 位置 | 说明文字写什么 |
|---|---|---|
| `01-login.png` | 登录页（地址填 bolt://127.0.0.1:7687） | TuGraph 管理界面登录，Bolt 通道 |
| `02-graphs.png` | 登录后的"图项目"页 | 可见 `medical_kg`(8点6边) 与 `medical_kg_en`(622点416边) |
| `03-schema.png` | 点 `medical_kg_en` → "图构建" | 5 类点 4 类边 + 属性定义 |
| `04-query.png` | "图查询"页执行 `MATCH (d:Disease)-[r:HAS_SYMPTOM]->(s:Symptom) WHERE d.name='acne' RETURN s.name, r.evidence` | 查询结果 + 证据字段 |

> 终端输出类的"截图"可以直接用代码块贴文字（更清晰），例如 `scripts/qa_cli.py -q "scabies 用哪些药？"` 的输出就是一张很好的"问答效果"素材，本次已实测：
> ```
> 问题> scabies 用哪些药？
> 生成 Cypher：MATCH (d:Disease)-[r:USES_DRUG]->(m:Drug) WHERE d.name = 'scabies' RETURN m.name AS drug, r.qa_id AS source, r.evidence AS evidence LIMIT 20
> 查询到 2 条记录：
> 1. cetirizine   来源：HE-13099  证据：I also suggest using Cetirizine to relieve the itching.
> 2. permethrin   来源：HE-13099  证据：I suggest using Permethrin cream as the first treatment.
> ```

### ④ 写 `homework3/README.md`（40 分钟，最大的一块）

按 PPT 五步组织，每步 = 命令 + 输出片段 + 截图 + 一段说明。可直接引用的现成数字在第 4 节；建议结构：

1. 环境部署（Bolt 连接测试输出、图列表、登录截图）
2. 图谱构建（数据解析统计 → 词典/规则/LLM 两路抽取 → P/R/F1 表 → 融合 622/416 → 六维质量分）
3. 基础 Text2Cypher（Prompt 结构、只读校验代码片段、`qa_cli.py` 问答输出）
4. 功能测试与对比（第 4.3 / ① 的两张表 + 结论）
5. 问题与总结（TuGraph 踩坑、词典召回瓶颈、关系精确率短板、证据溯源的价值）

### ⑤ 提交（5 分钟）
```bash
cd /home/mrio/Codes/homework_compre
git status                  # 注意：homework2Compre/res.html 的改动不是本次的，别顺手提交
git add homework3
git commit -m "homework3: 医疗知识图谱对话系统（TuGraph + Text2Cypher）"
git push
```
然后把仓库链接贴到学习通。**确认 `homework3/.env` 没有被提交**（`.gitignore` 已包含，但 `git status` 里仍要肉眼确认一次），里面的 API key 不能外泄。

---

## 6. 一键复现（从零开始，约 25 分钟，不含第①步的模型等待）

```bash
cd /home/mrio/Codes/homework_compre/homework3
P=/home/mrio/miniconda3/envs/normal/bin/python

docker start tugraph_demo && sleep 12          # 起库
$P scripts/test_connection.py                  # 1. 连接测试
$P scripts/init_schema.py                      # 2. 建 5 点 4 边
$P scripts/import_demo.py --reset              #    课堂样例 8/6
$P scripts/verify_graph.py                     #    自动验证 9/9

unzip -o -q Medical-Dialogue-Dataset-English.zip -d data/raw/   # 原始语料（若已解压跳过）
$P scripts/preprocess_data.py --limit 150      # 3. 解析+抽样
$P scripts/extract_ner.py --method rule_simple #    规则 NER（对照）
$P scripts/extract_ner.py --method rule_longest
$P scripts/extract_ner.py --method llm --limit 50        # 约 10 分钟
$P scripts/extract_re.py  --method rule
$P scripts/extract_re.py  --method llm --limit 50        # 约 10 分钟
$P scripts/evaluate_extraction.py              #    P/R/F1
$P scripts/build_kg.py --reset                 # 4. 融合建图（622/416）
$P scripts/quality_score.py                    #    六维质量分

$P scripts/init_schema.py --graph medical_kg_en
$P scripts/build_testset.py                    # 5. 测试集 11 条
$P scripts/qa_cli.py -q "scabies 用哪些药？"     #    交互问答
$P scripts/eval_text2cypher.py --modes baseline schema schema_aware   # 6. 对比实验（待跑）
```

---

## 7. TuGraph 踩坑记录（**接手必读，能省好几小时**）

1. **REST 接口用不了**：`http://127.0.0.1:7070/api/v1/login` 等所有 `/api/v1/*` 都返回 `Unauthorized: No token given in the request`，Basic/Bearer 都试过，签发 token 的入口抓不到。**结论：用 Bolt**（`bolt://127.0.0.1:7687` + neo4j 驱动），Web 管理台本身登录走的也是 Bolt，PPT 第 4 页也把 Bolt 列为第一选项。
2. **Bolt 的"图名"就是 database 字段**：`driver.session(database="medical_kg_en")`，没有默认图，不带图名会报 `Graph name cannot be empty`。
3. **`UNWIND $rows AS row ... SET n.x = row.x` 会把行值错位**（相邻行互相覆盖，实测 D001 拿到 D002 的 name）。**写入一律用"一条 Cypher 一个参数集"循环**，约 1ms/条，几百条无压力。封装在 `kg_common.upsert_vertices / upsert_edges`。
4. **建标签的语法是怪癖**：
   - 点：`CALL db.createVertexLabel('Disease','id','id','STRING',false,'name','STRING',true,'description','STRING',true)`（第 5 个参数是 **is_optional**）
   - 边：`CALL db.createEdgeLabel('HAS_SYMPTOM','[["Disease","Symptom"]]','qa_id','STRING',true,...)` —— **第二参数必须是 JSON 字符串**，传 list 会报 `X has to be a string`
   - 非主键字段设成 optional=true，否则 `MERGE (n {id})` 建点时因缺 name 报 `Field [name] is not optional`
5. **`count(DISTINCT x)` 返回空结果集**，且空结果会让 Bolt 连接变 defunct；`... LIMIT 0` 也会**把容器打成 SIGSEGV（Exited 139）**。已规避（`quality_score.py` 改为取回 id 本地去重），`kg_common.run()` 里加了断线重连。
6. **`dbms.graph.*` 过程可用**：`createGraph(name, desc, size_gb)` / `listGraphs()` / `deleteGraph()` / `getGraphSchema()`，所以建图、取 schema 都能在 Bolt 里完成，不必点界面（`text2cypher.py` 的 Schema 感知就是直接读 `getGraphSchema()`）。
7. **返回值里 `labels(n)` 是字符串**（形如 `"[Disease]"`），判断标签要用子串匹配。

---

## 8. 大模型调用的成本与稳定性

- 端点 `https://drc.totokens.cc/v1`，模型 `gpt-6.2-sol`（该端点只有 gpt-* / gpt-image-* 系列，**没有 qwen**，别照 PPT 写 `qwen-plus`，会报 `Model not supported by this group`）
- 实测：50 条 NER 约 570s、50 条 RE 约 620s，prompt token 消耗约 27 万/50 条（**输入很贵**，`qa_subset.jsonl` 里每条文本截到 2500 字符就是为了控成本，别随意放宽）
- 单次超时 120s，脚本内部有 3 次重试
- 想省钱：`--limit` 调小（NER/RE 各 20 条也能出结果，只是图表数字变粗）

---

## 9. 已知瑕疵（报告里坦诚写，别藏着）

1. 人工标注只有 **12 条**（PPT 课堂子集是 40 条），指标方差大 —— 报告里写清"抽样 12 条精标，用于方法间相对比较，绝对值仅供参考"
2. `medical_kg_en` 里有些实体质量不高：如 `pain during sex`、`itchy blisters` 被当成了疾病节点（LLM 抽取把症状描述当疾病），`amoxy`、`nsaid` 这类缩写也没完全规范化 —— 属于"抽取结果 ≠ 可信医学事实"的真实案例，正好用来支撑报告的讨论部分
3. 关系精确率只有约 26%，主因是规则层的线索词太宽（`consult`、`take` 命中率高但语义弱）
4. 英文数据集与 PPT 中文示例不一致：报告里要说明"中文课堂样例保留为 `medical_kg` 图用于验收对照，主实验用英文语料"
5. FastAPI 后端**没有做**（环境里没装 fastapi/uvicorn，按"简单搞"的要求跳过）。若老师坚持要 Swagger 演示：`pip install fastapi uvicorn` 后照 PPT 第 6 页写一个 `/api/disease/symptoms` 接口，内部直接调 `text2cypher.Text2Cypher.ask()`

---

## 10. 交接方最该先做的三件事

1. `docker start tugraph_demo` + `scripts/test_connection.py` 确认环境和数据还在（图 `medical_kg` / `medical_kg_en` 都在就算 OK）
2. 跑第 5 节第 ① 步，把 `results/text2cypher_eval.md` 跑出来
3. 边跑边按第 ④ 步的 5 段结构写 `README.md`，数字从第 4 节和第 ① 步结果里抄
