# GameBigR 设计文档

本文档从**设计思想抽象**、**当前实现映射**、**上线与真实数据下的可扩展性**三个维度描述 GameBigR。

---

## 1. 文档信息


| 项    | 说明                                        |
| ---- | ----------------------------------------- |
| 项目名称 | GameBigR                                  |
| 定位   | 游戏圈群策略验证与人群包编排                            |
| 核心设计 | 策略引擎、agent智能解析、Holdout 评估、Need 级在线学习，自我进化 |


---

## 2. 背景与要解决的问题

### 2.1 业务语境

在买量、活动、推荐等场景中，需要把「某款游戏的运营特征」转化为「可执行的人群圈选策略」，并在可解释的前提下持续优化效果。

### 2.2 核心矛盾

1. **可解释 vs 效果**：纯黑盒模型难审计；纯人工规则难覆盖长尾。
2. **绝对指标 vs 增量**：只看触达人群表现易**高估**策略贡献，需要对照组估计增量。
3. **数据稀疏 vs 决策刚性**：部分「组织参与度」等信号在画像侧不可直接观测，需**代理特征**并标注不确定性。

GameBigR 用「语义+ 规则 + LLM重写和加强 + Holdout 反馈+在线学习进化」组合缓解上述矛盾。

---

## 3. 设计目标（当前版本）


| 目标       | 说明                                                    |
| -------- | ----------------------------------------------------- |
| G1 结构化输入 | 支持 DB 画像与自然语言描述两种入口，统一到 `games` 语义。                   |
| G2 可解释决策 | Need 向量、规则 Tier、证据来源（direct / proxy / llm）可追溯。        |
| G3 可执行产出 | 输出多档 `SELECT` 人群包 SQL，便于对接数仓或投放侧。                     |
| G4 可验证   | 支持包级指标汇总；支持 treatment/holdout 的增量评估。                  |
| G5 可学习   | 将评估结果映射为 `rule_feedback`，更新 `need_weights` 影响下一轮需求向量。 |


非目标（当前明确未承担）：全量实时推荐服务、亿级用户在线打分、复杂因果图模型。

---

## 4. 核心设计思想（抽象层）

### 4.1 「本体驱动」而非「脚本堆砌」

- **游戏侧**：画像字段 → 离散标签（如高组织依赖、赛季制）→ 聚合为 **Need**（需求维度）。
- **人群侧**：Need → 绑定 `NEED_TO_RULE_TEMPLATES` 中的规则模板 → 生成 SQL 片段。

好处：新增品类时优先改**本体与模板**，而不是散落改 SQL 字符串。

### 4.2 「显式证据链」

每条规则携带 `evidence_source`：

- **direct**：`players` 上字段可直接过滤。
- **proxy**：目标列缺失或弱可用，用可观测行为列组合近似（见 `strategy_engine._proxy_expr`）。
- **llm_***：在强约束（列名、算子白名单）下由模型补充，失败则回退启发式。

设计意图：**任何一条圈人条件都能回答「凭什么」**。

### 4.3 「编排与计算分离」

- **LangGraph**（`agentic_workflow.py`）：负责阶段化、状态传递、轨迹记录。
- **策略与 SQL**（`strategy_engine.py`）：纯函数式逻辑，便于单测与替换。
- **评估与学习**（`evaluation.py`、`online_learning.py`）：与图编排解耦，可按活动维度独立演进。

### 4.4 「增量优于绝对」的评估

Holdout 不是「无数据」，而是**同一候选池内未触达子集**，用于估计自然基线；  
`treatment` 与 `holdout` 在相同画像分布下对比，**差值（uplift）** 比单纯触达组均值更接近「策略带来的边际贡献」。

### 4.5 「信用分配在可解释维度上」

当前将 campaign 级 reward 按 **need 强度** 分摊到各 need，再写入 `rule_feedback`。  
**学习发生在语义稳定的 need 空间**，而不是直接在每条 SQL 上暴力调参，便于运营理解与审计。

---

## 5. 系统架构

### 5.1 逻辑分层

```
输入层：games（结构化 / NL 抽取）、players（特征 + 标签）
    ↓
解析层：画像 → Need 向量（+ 业务目标微调 + need_weights 缩放）
    ↓
决策层：LangGraph（解析 → 选规则 → 可选 LLM 重写 → 证据 → 人群 SQL）
    ↓
评估层：包级 SQL 指标（模拟/准真实）+ 可选 Campaign Holdout 增量
    ↓
学习层：rule_feedback → need_weights → 回流解析层
```

### 5.2 LangGraph 状态机（`CircleState`）


| 字段                          | 含义              |
| --------------------------- | --------------- |
| `conn`                      | SQLite 连接       |
| `game_id` / `business_goal` | 运行上下文           |
| `game_profile`              | 当前游戏行展开为字典      |
| `needs`                     | 需求向量（已含目标调整与权重） |
| `strategy_output`           | 规则列表 + SQL 包    |
| `explain`                   | 证据统计、风险标记       |
| `trace`                     | 可观测执行轨迹         |


节点顺序：`game_parser_agent` → `rule_selector_agent` → `llm_rewriter_agent` → `evidence_agent` → `audience_builder_agent`。

### 5.3 对外入口

- `run_agentic_orchestrator(conn, game_id, business_goal)`：标准编排。
- `run_agentic_orchestrator_from_description(...)`：NL 抽取 + 可选入库 + 编排（调用方需自行保证评估所需数据已就绪）。

---

## 6. 数据模型概要

### 6.1 `games`

游戏产品级画像，是 Need 推断的**先验来源**。

### 6.2 `players`

- **行为 / 偏好特征**：用于规则 SQL 与代理表达式。
- `**simulated_*`**：演示用连续概率与 LTV，用于快速 `evaluate_sql` 包级对比。
- `**quasi_real_*`**：准真实标签（0/1 与 LTV），用于 **Holdout 观测** 与「真实反馈」演示链路，语义上对齐「窗口内回传标签」。

### 6.3 `campaign_assignments` / `campaign_observations`

- **assignments**：某活动、某包下，用户是否划入 holdout（`is_holdout`）。
- **observations**：该用户在活动口径下的安装 / D7 / 首充 / LTV30 观测值。

设计意图：把「**谁被划入实验**」与「**看到了什么结果**」拆开，便于后续接真实埋点流水、延迟到达、重算。

### 6.4 `rule_feedback` / `need_weights`

- `rule_feedback`：学习信号的**事实表**（当前粒度为 need；可演进为多粒度）。
- `need_weights`：按 `(business_goal, need)` 存储乘子，在 `game_parser_agent` 中与基础 need 向量逐元素相乘后再归一化。

---

## 7. 关键流程说明

### 7.1 Need 向量生成（`parse_game_to_needs`）（后续要根据真实数据调整）

1. 根据 `games` 行布尔条件打标签集合。
2. 查 `GAME_TO_NEED_RULES` 累加各 need 得分。
3. 按最大值归一化得到分布型向量。

### 7.2 业务目标微调（`_goal_adjustments`）（后续要根据真实数据调整）

在向量上对少数 need 做**固定增量**再归一化，体现「同一游戏、不同商业目标」下的策略侧重差异。

### 7.3 在线学习前置（`get_need_multipliers` / `apply_need_multipliers`）

读取历史反馈沉淀的 `need_weights`，对 need 向量做缩放并再次归一化，使**上一轮评估结论**影响下一轮规则优先级。

### 7.4 规则选择与 SQL 组装（`select_rules_and_build_sql`）

1. 对 need 按权重排序，截取 Top-K need。
2. 对每个 need 展开模板规则，结合 `PLAYER_AVAILABILITY` 决定 direct 或 proxy SQL。
3. 汇总为 `sql_packages`（强匹配 / 扩量 / 探索 / 不推荐）。

### 7.5 Holdout 与真实反馈（`evaluation` + `online_learning`）

1. `assign_campaign_with_holdout`：对某包 SQL 结果集随机划分 treatment / holdout，并写入 assignments + observations。
2. `evaluate_campaign_holdout`：比较两组在 `first_pay_label`、`ltv30_value` 上的均值差（uplift）。
3. `write_real_feedback_from_campaign`：将 uplift 映射到 [0.05, 0.95] 的 reward，再按 need 强度写入 `rule_feedback`。
4. `update_weights_from_feedback`：按 need 聚合平均 reward，用带上下界的增量规则更新 `need_weights`。

---

## 8. 非功能设计考量


| 维度     | 当前做法                                  | 意图             |
| ------ | ------------------------------------- | -------------- |
| 可测试性   | pytest 覆盖编排、实验导出、Holdout 链路           | 防止图编排回归        |
| 可迁移性   | DB 访问集中在 `db.connect` / `init_schema` | 注释已提示可换 OLAP   |
| LLM 安全 | 列名、算子、Tier 白名单                        | 防止任意 SQL 注入式生成 |
| 稳定性    | reward 与 weight 均有 clamp              | 防止单次活动噪声拖垮全局   |


---

## 9. 上线后真实数据：可扩展性思考

以下按**数据形态 → 系统能力 → 推荐演进路径**组织，便于与工程/数据团队对齐。

### 9.1 存储与计算规模

**现状**：单机 SQLite，适合 Demo 与单测。  
**扩展**：

- **画像与标签**：迁移至数仓（Hive/Spark）或 OLAP（ClickHouse、Doris），圈群改为「生成 SQL + 调度任务」或「导出 ID 列表到对象存储」。
- **在线服务**：决策与特征服务分离；规则包版本化（`policy_id`）；避免在请求路径直接跑重型 SQL。

### 9.2 标签与反馈语义

**现状**：`quasi_real_`* 由本地生成器模拟「准真实」；观测与 assignments 同事务写入。  
**扩展**：

- 明确 **归因窗口**（曝光后 7 日付费 vs 自然月付费），在 `campaign_observations` 增加 `metric_window`、`attribution_model`。
- **延迟标签**：观测异步到达 → 引入状态机（pending / partial / final）与重算任务，而不是单次 `INSERT` 即视为终态。
- **多触点**：holdout 需排除「被其他活动触达」的污染，需在 assignments 或独立表里记录 **曝光事实**。

### 9.3 Holdout 与因果推断成熟度

**现状**：简单随机分层、二元/标量指标、点估计 uplift。  
**扩展**：

- **分层随机**：按 region / 渠道 / 价值分桶后再 holdout，减小方差。
- **显著性**：Wilson 区间、Bootstrap、贝叶斯后验，避免小样本噪声驱动 `need_weights`。
- **更因果**：倾向得分匹配、uplift 模型（T-learner / X-learner）；将「圈群 SQL」作为 treatment 定义的一部分写入元数据。

### 9.4 学习信号粒度

**现状**：need 级 reward，聚合为 `AVG(reward_score)` 更新乘子。  
**扩展**：

- **规则级 / 包级** feedback：`rule_feedback` 增加 `rule_id`、`package_name`、`sql_hash`。
- **多目标**：按业务目标维护不同 reward 配方（ROI、留存、成本约束），或标量化后做 Pareto 筛选。
- **冷启动与遗忘**：对 `need_weights` 引入指数衰减或贝叶斯先验，防止历史活动永久绑架新游戏。

### 9.5 本体与策略的可演进性

**现状**：静态 `ontology.py` + 手写阈值。  
**扩展**：

- 本体版本化（`ontology_v2`），与线上策略包绑定。
- 标签→need 权重由离线拟合定期回灌，仍保留规则模板作为**护栏**。

---

## 10. 演进路线建议（优先级）

1. **观测链路产品化**：曝光日志 → assignments；转化日志 → observations（支持延迟与重算）。
2. **统计门槛**：uplift 置信区间未过阈值则不写 `rule_feedback` 或仅写 shadow 表。
3. **反馈粒度下沉**：rule / package 级 credit assignment，再聚合到 need。
4. **计算与存储下沉**：SQLite → 数仓 SQL + 工作流调度；图编排保留为「策略编排服务」。

---

## 11. 与仓库文件的对应关系


| 主题       | 主要文件                             |
| -------- | -------------------------------- |
| Schema   | `src/db.py`                      |
| 种子与准真实标签 | `src/data_builder.py`            |
| 编排       | `src/agentic_workflow.py`        |
| 本体与规则模板  | `src/ontology.py`                |
| 策略与 SQL  | `src/strategy_engine.py`         |
| LLM      | `src/llm_rules.py`               |
| 自然语言画像   | `src/game_description_parser.py` |
| 包级评估     | `src/evaluation.py`              |
| 反馈与学习    | `src/online_learning.py`         |
| 实验导出     | `src/experiments.py`             |
| 端到端 Demo | `run_e2e.py`                     |


---

## 12. 文档维护

当以下任一发生变更时，应同步更新本文档：**数据表语义**、**Holdout 策略**、**reward 映射**、**LangGraph 节点**、**对外 API**。README 侧重快速上手；本文档侧重设计与演进依据。