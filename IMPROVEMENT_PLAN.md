# 运维数字员工项目改进方案

## 目标

把当前项目中被扣分的三个点改造成可展示、可测试、可渐进演进的工程能力：

1. 多 Agent 从“确定性角色编排”升级为“可启用的真实 subagent LLM 审阅 + 确定性安全门控”。
2. RAG 从纯词法 chunk 检索升级为“词法召回 + 语义特征召回 + 重排”的 hybrid RAG。
3. 知识去重/合并从 hash/ngram 启发式升级为“精确 hash + 近似签名 + 语义关系 + 差异摘要”的审核流水线。
4. 用户输入从“直接进 RAG”升级为“intent router 范围控制 + fallback/out-of-scope/low-information 分流”。

这些改动必须保持课程项目的离线可运行性：没有 GPU、没有 vLLM、没有向量数据库时，后端测试仍然可通过；部署真实 Qwen/vLLM 后，可以打开增强能力。

## 参考方案

- ReAct: reasoning and acting trace, suitable for tool-using agent workflows.
- AutoGen: multi-agent conversation framework, suitable for multiple role agents.
- MetaGPT: SOP-style role collaboration, suitable for operations workflows.
- Qwen-Agent: Qwen-native agent framework, suitable for future integration with local Qwen and tools.
- DPR/RAG/ColBERTv2/Self-RAG: retrieval and reranking design references.
- Qwen3-Embedding/BGE-M3: future dense embedding and reranker candidates.
- FAISS: current local vector index backend for dense candidate recall.
- Rasa out_of_scope/fallback/handoff: current intent router behavior reference.
- Haystack ConditionalRouter: current deterministic branch-routing reference.
- LlamaIndex RouterQueryEngine: future multi-retriever routing reference.
- NVIDIA NeMo Guardrails topical rails: future heavier policy rail reference.
- Qdrant/Milvus: future distributed vector store candidates.
- SimHash/MinHash/SBERT: near-duplicate and semantic-similarity references.

## 子目标与状态

| 子目标 | 状态 | 验收标准 |
| --- | --- | --- |
| 0. 改进方案文档 | Completed | 根目录存在本文件，包含设计、阶段、验收标准和风险控制 |
| 1. 真实 subagent LLM 审阅通道 | Completed | agent status 暴露开关；开启后每个 subagent 可基于自己的 prompt 产出 JSON 审阅；关闭时保持确定性流程 |
| 2. Qwen3 Embedding RAG 与重排 | Completed | RAG 输出包含 Qwen3 embedding strategy、embedding/lexical/rerank 分数；RAG 评估仍通过 |
| 3. 知识去重/合并增强 | Completed | duplicate check 输出近似签名、语义关系和差异摘要；自动入库继续遵守 RBAC/审核边界 |
| 4. 意图路由与范围控制 | Completed | 问候、低信息、无关问题不触发 RAG；运维问题继续进入 RAG；高风险绕过/提权请求进入受控链路 |
| 5. 测试与交付验证 | Completed | 后端 pytest 通过；前端 typecheck 通过或明确说明未运行原因；package_check 通过 |

## 设计原则

- LLM 不做最终安全裁决。账号、权限、生产、数据库、删除、批量、root/sudo 等高风险动作仍由确定性 risk gate、RBAC、审批流和审计控制。
- subagent LLM 输出只作为 review/advice 写入 trace metadata，不直接绕过后端工具边界。
- Intent router 默认使用确定性规则处理范围控制；可选 LLM 路由只作为边界输入的 JSON 建议，不覆盖高风险拦截。
- Hybrid RAG 使用本地 Qwen3-Embedding dense retrieval，并保留关键词、标题、标签作为保守重排信号；embedding index 默认以 numpy 文件持久化，FAISS 可用时作为本地 dense candidate recall 后端，Qdrant/Milvus 作为后续分布式向量库扩展点。
- 知识去重采用多阶段流水线：canonical hash 处理精确重复，近似签名处理文本改写，语义关系处理同义表达，差异摘要辅助人工审核。
- 所有增强都要可观测：status、metadata、score_detail、duplicate_check 都要说明使用了哪些策略。

## 阶段 1：真实 Subagent LLM 审阅通道

### 当前问题

`AgentService` 已经有 supervisor、risk_guardian、ops_employee、knowledge_curator、evaluator 五个角色和英文 prompt 文件，但核心决策仍主要来自确定性 Python 逻辑。答辩时容易被认为只是“多角色标签”，不是 Agent。

### 改造方案

- 增加配置项：
  - `OPS_ENABLE_AGENT_LLM=false` 默认关闭，保证离线测试稳定。
  - `OPS_AGENT_LLM_TIMEOUT_SECONDS` 控制真实 subagent 调用超时。
- 在 `LLMService` 增加结构化 JSON 调用入口：
  - 输入：agent name、system prompt、task、state、schema hint。
  - 输出：`ok/status/content/json/error`。
- 在 `AgentService` 增加 `llm_reviews`：
  - 每个 subagent 使用自己的 `prompt.md` 和当前 workflow state 生成 JSON review。
  - review 不改变最终安全决策，只作为可审计建议。
  - vLLM 不可用时记录 fallback，不中断业务。
- `/api/agent/status` 返回增强状态和已加载 prompt。

### 验收

- 默认关闭时现有测试不需要 vLLM。
- 开启并 mock LLM 时，可以看到每个 subagent 的 JSON review。
- 高风险请求仍然由确定性 gate 强制转人工。

## 阶段 2：Qwen3 Embedding RAG 与重排

### 当前问题

当前 RAG 已从旧的关键词/本地语义特征方案升级为本地 Qwen3-Embedding dense retrieval。关键词、标题、标签仍作为保守 rerank 信号，但不再使用旧的伪 semantic feature 作为主逻辑。

### 改造方案

- 使用 `models/qwen3-embedding-0.6b` 作为本地 dense embedding 模型。
- 查询侧使用英文 instruction：enterprise IT operations support query -> private knowledge passages。
- 文档侧直接 embedding 知识标题、标签和 chunk 正文。
- 搜索阶段改为：
  - embedding score：Qwen3 embedding cosine similarity。
  - lexical score：IDF overlap/title/tag/phrase 作为 hybrid rerank 信号。
  - final score：融合 embedding、lexical、phrase、tag 和轻量 rerank boost。
- 返回 `strategy=qwen3_embedding_hybrid_rerank` 和完整 `score_detail`。
- 文档 chunk embedding 写入 `OPS_EMBEDDING_INDEX_DIR`，进程重启后优先加载同一知识签名、模型路径和维度匹配的持久化索引；知识发布、下线、编辑或 embedding 配置变化时自动重建。
- embedding 显式关闭或不可用时，才降级为 `keyword_rerank_fallback`，用于测试和故障兜底。

### 验收

- `/api/rag/evaluate` pass_rate 仍为 1.0。
- QA 返回的 RAG metadata 包含新 strategy 和 embedding score。
- 无引用问题仍不能触发 LLM 编造。

## 阶段 3：知识去重/合并增强

### 当前问题

当前去重使用 canonical hash、ngram、Jaccard、containment，能覆盖明显重复，但对同义改写、范围差异、补充步骤、冲突内容的解释不足。

### 改造方案

- 增加近似签名：
  - SimHash-style fingerprint。
  - MinHash-style signature estimate。
  - 输出 `approx_similarity`。
- 增加语义关系判断：
  - exact duplicate
  - near duplicate
  - same problem with new solution
  - different scope
  - possible conflict
  - unique
- 增加 diff summary：
  - `novel_units`
  - `shared_terms`
  - `conflict_signals`
  - `recommended_action`
- 自动入库继续沿用：
  - exact skip
  - redundant skip
  - safe merge
  - pending merge candidate
  - unique insert

### 验收

- duplicate check 返回增强字段。
- 自动入库测试继续覆盖 skip/merge/candidate。
- 运维角色不能直接修改已发布知识，管理员发布仍检查敏感信息和重复。

## 阶段 4：测试与交付

- 新增或更新测试：
  - agent status/review metadata。
  - RAG strategy 和 score_detail。
  - semantic duplicate/diff summary。
- 运行：
  - `python -m pytest`
  - `pnpm --filter ops-employee-frontend typecheck`
  - `python scripts/package_check.py`
- 更新本文件状态。

## 风险与控制

- vLLM 不可用：subagent LLM review 记录 fallback，不影响确定性流程。
- semantic fallback 误召回：保留最低分阈值和无引用 fallback。
- 自动合并误合并：只有管理员、已发布目标、达到阈值时才可直接合并；否则生成待审核候选。
- 性能膨胀：当前保持 SQLite + 本地持久化 embedding index；FAISS 作为本地向量召回后端，Qdrant/Milvus 作为二期分布式部署，不阻塞课程交付。
