# Chat BI — 智能对话式 BI 平台

## 背景

传统 BI（报表、拖拽式看板）门槛高：取数需要懂数据表结构、会写 SQL，业务人员只能等数据团队排期。希望做一个对话式 BI 平台，用户用自然语言提问，系统自动生成并执行 SQL，返回查询结果、数据分析结论或导出文件，同时保证查询安全（防越权、防大查询）与过程可观测。

## 核心想法

一句话：**用户在对话界面用自然语言提问，后台结合表结构知识库生成 SQL，经安全检查后执行，按需返回数据集 / 分析结论 / Excel 导出。**

按四个模块展开：

1. **数据表结构获取模块（schema 采集）**：定时或 trigger 触发，读取全部或指定表的表结构（库/表/字段/注释/类型/主外键/采样值），解析后存储到知识库/向量数据库，供后续 SQL 生成检索。
2. **智能对话 UI**：用户登录页签 + 智能对话页面，对接 BI 后台完成多轮对话与结果展示（数据表格、结论文本、文件下载）。
3. **智能 BI 后台（NL2SQL + 安全执行）**：解析用户输入 → 结合 schema 知识库检索相关表 → 生成 SQL → 安全检查（SQL 语法/注入检查、执行计划检查、单次查询数据量限制、基于用户的数据权限越权检查）→ 调用数据库连接执行 → 结果后处理：
   - 用户要结论 → 对查询结果做数据分析，输出分析结论（不返回原始数据集）
   - 用户要明细 → 数据集展示 / 导出 Excel
4. **智能体步骤观测**：全链路 trace（schema 检索、prompt 构造、LLM 调用、SQL 检查、执行、后处理），使用 LangSmith 做 trace 观测与评测（2026-10-31 定稿，替换原 LangSmith 方案）。

## 技术方案

- 待定，初步倾向：
  - **schema 知识库**（2026-10-31 讨论定稿：不用完整 GraphRAG，用「结构化主存储 + 混合检索」的 schema linking 路线）：
  - 图关系必须显式建模（FK/join 路径是 SQL 生成核心难点），但 GraphRAG 面向「从非结构化文本抽实体关系」的场景，schema 的图天然显式存在，不需要 LLM 抽取，属于过度设计；
  - 分层方案：元数据主存储（关系库存 DDL/列注释/PK/FK/join 关系/采样值/业务口径指标定义）→ 向量召回表卡片 → join 图 1~2 跳扩展补齐关联表 → 值索引（query 关键词 vs 采样值）→ 检索层按用户数据权限预裁剪；
  - 规模判断：表 < 100 直接全量 schema 进 system prompt（配 prompt caching），不建检索；几百~几千表才上检索管线并加 schema linking 短名单步骤；
  - 业界参考：Vanna.ai / DB-GPT 均为「向量召回 DDL + 显式 join 关系」，无一家用完整 GraphRAG 做 schema。
  - **技术栈**（2026-10-31 定稿）：Python + FastAPI + LangChain/**LangGraph**。LangGraph 承载编排：StateGraph 节点映射 rewrite/检索/生成/检查/执行/路由流水线，条件边 + 循环实现幻觉自愈 bounded 重试，Checkpointer（Postgres）承载会话状态（working_set/history/digest 即 graph state，免建独立 Redis 会话层），interrupt 实现 clarify 快捷按钮追问（human-in-the-loop）。
- **观测与评估**：LangSmith——环境变量接入（零代码 trace）；核心加分是把沉淀的 question-SQL 对做成 datasets，prompt/检索策略/知识库变更后跑回归评估。注意：LangSmith 为 SaaS 优先（self-host 需企业版），查询/SQL/schema 出境需确认合规；本机已有 LangSmith v4 部署可作回退。
  - **SQL 安全检查**：语法解析（如 sqlglot / JSqlParser）→ 白名单校验（只允许 SELECT）→ EXPLAIN 执行计划 → 数据量/超时限制 → 行级数据权限（用户-表/行/列权限映射）在 SQL 改写或代理层强制注入；
  - **观测**：LangSmith（OpenTelemetry 接入，本地已有 v4 部署）记录每步 trace、prompt、token、SQL 与结果摘要；
  - **导出**：查询结果流式写入 Excel（如 openpyxl / EasyExcel），大结果集分批导出。
- 架构分层、具体选型、模块间接口在后续讨论中细化。

## 待解决问题

- 业务口径/指标定义（2026-10-31 讨论定稿：LLM 起草 + 人工审核，分级覆盖）：口径是最大质量杠杆也是静默错误源（口径歧义无机器信号可检测，必须在生成前消除）。两类对象——列级语义（definition/unit/synonyms）与指标级口径（一等公民：name/aliases/definition/expression/base_filter/time_semantics/owner/version/effective_date，进表卡片 embedding + 别名索引双通道）。录入分级：核心指标人工定义为准；核心业务表全列 LLM 起草人工审核后生效；长尾表 LLM 自动生成标 `auto` 低置信度，按 LangSmith 查询频率升级进审核队列。运行时规则：指标别名精确命中 → 规范表达式作为硬约束注入（禁止 LLM 自行改写口径）；歧义命中多个候选 → clarify 快捷按钮选口径；指标版本化（每次查询记录所用版本，支撑「和报表对不上」的 diff 归因）+ 纠错回流触发口径复审。明确不引入 Cube.js/dbt Semantic Layer 等语义层引擎（YAGNI，一张 metric 表够用）。
- 表结构增量更新（2026-10-31 讨论定稿：定时全量 diff 为主 + query-time 兜底，不做 binlog CDC）：表结构变更实时性要求低，CDC 基础设施重且只覆盖 MySQL 类源（CH/Hive/Presto 无可用通道），收益成本倒挂。三种触发：① 每日夜间全量拉 information_schema 与元数据存储 diff（元数据量小、秒级，每表算 schema hash，无变化零下游动作）；② 单表手动刷新 API/UI（新表接入、改表后立即要查）；③ query-time 兜底——生成的 SQL 在库层报「列/表不存在」时，修复循环第一步先对涉及表定向刷新再重试，使定时同步可安心每日一次（最终一致）。diff 按表粒度级联生效：DDL 变更 → 更新主存储 + 增量重嵌入表卡片；新增列 → 按分级规则起草注解；删列/改名 → 注解归档 + 依赖分析（解析 metric.expression / question-SQL 引用列）→ 受影响指标标记「需复审」且复审前停止注入硬约束（宁可退化不注入，不错误注入旧口径）、受影响 question-SQL 对标记 stale 不作 few-shot。附带：每表 schema 快照版本化 + 变更历史（归因「之前能查现在不能」）、删列/改名进 owner 告警队列。
- 多轮对话上下文维护（2026-10-31 讨论定稿：结构化会话状态，不是历史消息全量塞 prompt）：session 维护 working_set（最近 SQL、涉及表/字段足迹、失败时的 error、结果 digest——只存行数/列/聚合摘要，永不存原始结果行）+ 最近 3~5 轮结构化 history + 更早轮次的 rolling_summary + 输出格式偏好，Redis/DB 持久化带 TTL。每轮前置一个轻量 LLM rewrite 节点，一次调用产出：rewritten_query（自包含问题，消解指代省略）、is_new_topic（新话题则重置 working_set 防旧表污染）、output_signal（复用输出路由判定）。schema 检索时 working_set 的表优先保留并与本轮检索结果取并集（跟进式问题不重新全量检索）。改错重试复用 last_error 走幻觉自愈链路。rewrite、话题检测、输出信号三合一，不增加额外调用成本。待细化：digest 的聚合摘要由谁生成（执行后处理自动算 vs LLM 生成）、rolling_summary 的压缩策略与触发时机。
- SQL 安全检查的具体策略：执行计划检查的判定标准（全表扫描阈值？）、单次查询数据量如何预估（EXPLAIN rows）与硬限制（LIMIT 注入 / 游标分批）？
- 数据权限模型（2026-10-31 讨论定稿：防御纵深，三层职责分离）：① 连接账号最小权限（只读 + 仅授权库）为底线；② 执行层代理为硬边界——SQL 生成后、执行前用 sqlglot 等 AST 校验表/列在用户权限内，行级条件由代理注入（确定性规则不交给概率性的 LLM）；③ 生成层只做「不检索越权表 + prompt 提示」，定位是语义正确与体验，不承担安全责任。权限规则单一来源为权限配置表，生成层裁剪与代理层注入均从它读取。待细化：多数据源方言差异下的代理改写实现、越权报错的用户侧友好提示。
- 分析结论与「要数据」的输出路由（2026-10-31 讨论定稿：两种都保留，不做前置硬分类，做「结果后处理路由」）：SQL 执行与数据集获取只有一条路，路由发生在结果后处理阶段——显式规则优先（导出/excel/明细 → 数据集；分析/结论/为什么 → 结论）→ 无信号默认「结论 + 预览前 N 行 + 导出按钮」→ 规则未命中且含糊时轻量 LLM 判定（conclusion/dataset/both/clarify）→ 追问仅在高代价场景触发且用 UI 快捷按钮；输出格式偏好随会话保持。工程细节：大结果集预览 + 异步生成 Excel（对话内给下载链接）；结论分析流式输出与预览并行渲染。
- LLM 选型与幻觉控制（2026-10-31 讨论定稿：只自愈有确定性错误信号的幻觉，三道防线）：
  - 幻觉分类：不存在表/列、方言语法错误、执行期错误（超时/超量）→ 自动自愈；语义性错误（join 写错但合法）→ 不自愈，靠 few-shot 与指标口径沉淀缓解；空结果/反常 → 不自愈，仅提示用户确认（YAGNI）；
  - 防线一（预防）：prompt 内 DDL 全部来自知识库检索（无编造源）+ 系统规则限定只用上下文对象 + 低 temperature；
  - 防线二（拦截，零 LLM token）：生成后、执行前 AST 校验（sqlglot 与权限校验同一解析点，一次解析两用）——表/列存在性、目标方言合法性，直接抓掉大部分硬幻觉，失败不进重试先走修复；
  - 防线三（重试，上限 2~3 次）：核心是结构化错误反馈——原始 SQL + 具体错误（"列 X 不存在"）+ 相似列名候选线索 + 精确 DDL 子集；每次失败将「上次尝试+上次错误」累积进反馈；执行期错误反馈带执行计划与阈值让 LLM 收窄查询；中间尝试只留本轮临时上下文，不进会话 history；
  - 兜底：超上限降级——可澄清转用户追问（快捷按钮），不可澄清（对象全 schema 不存在）直接说明不存在或无权限；
  - 沉淀与观测：修复成功的 (问题, 最终 SQL) 回流知识库 question-SQL 对（自愈终态是不再需要自愈）；LangSmith 记录 retry 次数与失败原因分类，同一对象反复修复 → 定向补录该表注释/口径。待细化：AST 校验支持的目标方言清单、相似列名候选的匹配方式（编辑距离 vs embedding）。
- 数据源范围（2026-10-31 定稿：只实现 MySQL，多实例配置）：固定 MySQL 简化 AST 方言（sqlglot MySQL dialect）、EXPLAIN 执行计划（rows 估算数据量）、方言校验、information_schema 采集与 diff；MySQL 无原生 RLS 与「代理层注入」结论一致。扩展留白：sqlglot 天然多方言，未来加 PG/ClickHouse 只需在采集、EXPLAIN、校验三处各加方言分支，不预建抽象层。需支持多个 MySQL 实例/业务库配置（地址/端口/只读账号），数据源作为元数据主存储的一等实体，schema 采集与查询执行均挂在数据源 ID 上；凭据走环境变量/密钥管理，禁止硬编码。

## 工程落地（MVP）

### MVP 分期

- **M1（已落地）**：schema 手动触发采集（information_schema → chatbi_meta）→ 全量 schema DDL 进 prompt（演示库表少，不建检索）→ LangGraph 流水线（generate → validate → execute → summarize）→ 返回 SQL + 数据集前 50 行 + 摘要。validate = sqlglot MySQL 方言解析 + 只允许单条 SELECT + 表/列存在性 + LIMIT 注入/钳制；execute 前 EXPLAIN rows 估算超阈值拒绝。后端 API 先行，UI 后置。
- **M2（已落地，2026-10-31）**：
  - pgvector 表卡片检索 + join 图扩展（列名 `<x>_id` 约定推导关联边，1 跳 BFS），embedding 未配置时自动降级全量 schema；`chat-bi/.env` 配 `EMBEDDING_*` 后 `POST /api/schema/collect` 即建索引；
  - 口径硬约束：metric 别名确定性匹配（`status='active'` 才参与），命中即注入规范表达式 + base_filter，LLM 禁止改写；
  - 多轮会话：PostgresSaver checkpointer（与 pgvector 共用连接池），rewrite 三合一节点（指代消解 rewritten_query / is_new_topic 工作集重置 / output_signal 输出信号）；`POST /api/chat` 传 `session_id` 续聊；
  - 权限预裁剪：`user`（grant_all_tables）+ `user_table_permission`，API `X-User-Id` 头（默认 1=全量授权），检索与 validate 共用权限来源；
  - 实施期修复：派生表别名（`FROM (SELECT...) t`）与 CTE 同等放行；checkpointer 持久化失败状态污染后续轮次 → rewrite 节点每轮开局清空 error 标记；PostgresSaver.setup 需连接池 autocommit（CONCURRENTLY 索引限制）。
- **M3**：幻觉自愈 bounded 重试 + clarify 降级追问（LangGraph interrupt）；输出路由（结论/明细/Excel 异步导出走 Redis 队列）；schema 增量 diff（定时 + query-time 兜底刷新）；LangSmith datasets 回归评估。

### 基础设施（docker-compose）

| 服务 | 端口映射 | 用途 | 启用版本 |
|---|---|---|---|
| mysql:8.4 | 13306 | chatbi_meta 元数据库 + business_demo 模拟业务库（init SQL 种子数据） | M1 |
| pgvector/pgvector:pg17 | 15432 | 向量检索 + LangGraph checkpointer | M2 |
| redis:7-alpine | 16379 | 导出任务队列 / 缓存 | M3 |

凭据全部走 `.env`（模板见 `.env.example`），LLM 为通用 OpenAI 兼容网关（`LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL`）。

### 启动步骤

```bash
cd chat-bi
docker compose up -d                     # 基础设施（MySQL 首次启动自动执行 init SQL）
cp .env.example .env                     # 首次：填入 LLM_API_KEY 等

cd backend
python -m venv .venv                     # 首次
.venv/Scripts/pip install -e ".[dev]"    # Windows；mac 用 .venv/bin/pip
.venv/Scripts/pytest                     # 单测（不依赖 LLM 与外部服务）
.venv/Scripts/uvicorn app.main:app --port 8600   # 在 backend/ 目录下执行
```

### 接口验证（curl）

```bash
# 采集表结构（business_demo → chatbi_meta）
curl -X POST http://localhost:8600/api/schema/collect

# 对话查数（中文 payload 建议走文件避免 shell 转义问题）
echo '{"question": "查一下订单金额最高的前 5 个用户及其大区"}' > /tmp/q.json
curl -X POST http://localhost:8600/api/chat -H "Content-Type: application/json" -d @/tmp/q.json
```

### 后端目录

```
backend/
├── pyproject.toml            # 依赖与 pytest 配置
├── app/
│   ├── main.py               # FastAPI 入口（/api/health）
│   ├── config.py             # pydantic-settings 读 .env
│   ├── db.py                 # meta/business 双引擎
│   ├── models.py             # 元数据库表定义（与 01-meta.sql 一致）
│   ├── collector.py          # information_schema 采集 → 元库（表级 upsert + 列级重插，幂等）
│   ├── prompt.py             # 元库记录 → CREATE TABLE DDL 文本
│   ├── security.py           # sqlglot 静态校验 + LIMIT 注入/钳制
│   ├── api/                  # /api/schema/collect、/api/chat
│   └── graph/                # LangGraph：state / nodes / build
└── tests/                    # security、collector 单测（sqlite 内存库隔离）
```
